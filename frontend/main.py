import base64
import collections
import datetime
import enum
import os
import re
import sys
import threading
import time
import typing as typ
import urllib
import urllib.error

import IPython.display as ipydisplay
import fileupload
import imageio
import matplotlib.pyplot as plt
import plotly
import plotly.graph_objs
import pystache
import seaborn as sns
import vtna.data_import
import vtna.filter
import vtna.graph
import vtna.layout
import vtna.node_measure
import vtna.statistics
import vtna.utility
from ipywidgets import widgets


def help_widget(text, style='') -> widgets.HTML:
    help_icon = f'<img class="helpwidget" ' \
                f'     title="{text}" ' \
                f'     onload="helpwidget_onload(this)" ' \
                f'     onmouseover="helpwidget_onmouseover(this)" ' \
                f'     onclick="helpwidget_onclick(this)" ' \
                f'     onmouseout="helpwidget_onmouseout(this)" ' \
                f'     src="images/help.png" ' \
                f'     style="max-width: 16px; {style}" ' \
                f'     alt="help icon"/>'
    return widgets.HTML(help_icon)


# Global container for all help texts.
HELP_TEXT = {
    'graph_upload': "<b>Interactions</b>:<br>"
                    "Tab/Whitespace-separated as text or compressed.<br>"
                    "No header.<br>"
                    "Col. 1: Timestamp (int), Col. 2: Node (int), Col. 3: Node (int).<br>"
                    "All other columns are ignored.<br>"
                    "<b>Example</b>:<br>"
                    "<code>"
                    "23940 1152 1089<br>"
                    "23940 1152 1228<br>"
                    "23960 1127 1146<br>"
                    "23980 1152 1228</code><br>",
    'metadata_upload': "<b>Attributes</b>:<br>"
                       "Tab/Whitespace-separated as text or compressed.<br>"
                       "No header.<br>"
                       "Col. 1: Node (int).<br>"
                       "Following columns are interpreted as nominal attributes.<br>"
                       "<b>Example</b>:<br>"
                       "<code>"
                       "954 2BIO1 F<br>"
                       "859 2BIO1 M<br>"
                       "489 2BIO1 F<br>"
                       "991 2BIO1 M<br>"
                       "</code>",
    'granularity': '<b>Interval length</b>: Width of time interval of each displayed frame.<br> '
                   'Interactions in each interval are aggregated.<br>',
    'column_ordinal_config': 'Select <b>Ordinal</b> to allow range queries for highlighting/filtering nodes.<br>'
                             'Drag and drop to change order of categories. Order is <em>ascending</em>.',
    'queries': "<b>Queries</b>: A series of clauses connected with operators, used to filter or highlight certain"
               " nodes by attribute values.<br>"
               "<b>Filter</b>: the nodes are filtered out of the graph.<br>"
               "<b>Highlight</b>: the nodes are only highlighted in the graph.<br><br>"
               "**Filter and highlight queries are maintained separately .<br>"
               "**In case of conflict/overlap of queries, the oldest query overrides the youngest.",
    'measures_selection': '<b>Local</b> measures will be computed for each time interval, and '
                          'only in regard to the nodes and edges existing there.<br>'
                          '<b>Global</b> measures refer to the aggregated super graph over all '
                          'timesteps.<br><br>'
                          'Note that some centralities might take a long time to compute.',
    "statistics":"Different types of plots are provided depending on the type of the attribute<br>"
                 "Interval/numerical attributes are shown as histograms <br>"
                 "Categorical/Ordinal attributes are shown as horizontal bar charts"
}

TOOLTIP = {
    'toggle_local_upload': 'Load file from local directory',
    'toggle_network_upload': 'Load file from URL',
    'graph_upload_button': 'Upload interactions over time',
    'metadata_upload_button': 'Upload node attributes',
    'back_to_import_button': 'Open Import view. Resets all graph display settings.',
    'apply_queries_to_graph_button': 'Apply filters and highlights to displayed graph',
    'add_query_button': 'Add Query with positive initial predicate',
    'add_neg_query_button': 'Add Query with negated initial predicate'
}


# Not a good solution, but "solves" the global variable problem and replaces it with singletons basically.
class UIDataUploadManager(object):
    NETWORK_UPLOAD_PLACEHOLDER = 'Enter URL -> Click Upload'  # type: str
    LOCAL_UPLOAD_PLACEHOLDER = 'Click on Upload -> Select file'  # type: str
    UPLOAD_DIR = 'upload/'  # type: str

    class UploadOrigin(enum.Enum):
        LOCAL = enum.auto()
        NETWORK = enum.auto()

    def __init__(self,
                 # Run button switches to Display graph step, should be disabled by default and enabled on set
                 # granularity.
                 run_button: widgets.Button,
                 # Graph upload widgets
                 local_graph_file_upload: fileupload.FileUploadWidget,
                 network_graph_upload_button: widgets.Button,
                 graph_data_text: widgets.Text,
                 graph_data_output: widgets.Output,
                 graph_hist_output: widgets.Output,
                 graph_data_loading: 'LoadingIndicator',
                 # Metadata upload widgets
                 local_metadata_file_upload: fileupload.FileUploadWidget,
                 network_metadata_upload_button: widgets.Button,
                 metadata_text: widgets.Text,
                 metadata_output: widgets.Output,
                 metadata_loading: 'LoadingIndicator',
                 # Metadata configuration widgets
                 metadata_configuration_vbox: widgets.VBox,  # Container, for configuration of each separate column
                 metadata_ordinal_help: widgets.HTML,
                 column_configuration_layout: widgets.Layout,  # Layout, for each separate column configuration
                 # Graph data configuration widgets
                 graph_data_configuration_vbox: widgets.VBox,  # Container, for configuration of graph data
                 measures_select_box: widgets.Box  # Container, for selecting wanted measures
                 ):
        self.__run_button = run_button
        run_button.disabled = True

        self.__local_graph_file_upload = local_graph_file_upload
        self.__network_graph_upload_button = network_graph_upload_button
        self.__graph_data_text = graph_data_text
        self.__graph_data_output = graph_data_output
        self.__graph_hist_output = graph_hist_output
        self.__graph_data_loading = graph_data_loading

        self.__local_metadata_file_upload = local_metadata_file_upload
        self.__network_metadata_upload_button = network_metadata_upload_button
        self.__metadata_data_text = metadata_text
        self.__metadata_data_output = metadata_output
        self.__metadata_loading = metadata_loading

        self.__metadata_configuration_vbox = metadata_configuration_vbox
        self.__metadata_ordinal_help = metadata_ordinal_help
        self.__metadata_ordinal_help.layout.display = 'none'
        self.__column_configuration_layout = column_configuration_layout

        self.__graph_data__configuration_vbox = graph_data_configuration_vbox

        self.__edge_list = None  # type: typ.List[vtna.data_import.TemporalEdge]
        self.__metadata = None  # type: vtna.data_import.MetadataTable

        self.__granularity = None

        self.__measure_selection_checkboxes = None  # type: typ.Dict[str, widgets.Checkbox]

        self.__order_enabled = {}  # type: typ.Dict[int, bool]

        # Show hints as placeholders
        self.__graph_data_text.placeholder = UIDataUploadManager.LOCAL_UPLOAD_PLACEHOLDER
        self.__metadata_data_text.placeholder = UIDataUploadManager.LOCAL_UPLOAD_PLACEHOLDER

        # Make sure the upload directory exists, create it if necessary
        if not os.path.isdir(UIDataUploadManager.UPLOAD_DIR):
            os.mkdir(UIDataUploadManager.UPLOAD_DIR)

        self.__display_measure_selection(measures_select_box)

    def get_edge_list(self) -> typ.List[vtna.data_import.TemporalEdge]:
        return self.__edge_list

    def get_metadata(self) -> vtna.data_import.MetadataTable:
        return self.__metadata

    def get_granularity(self) -> int:
        return self.__granularity

    def get_selected_measures(self) -> typ.Dict[str, bool]:
        return dict([(name, checkbox.value) for name, checkbox in self.__measure_selection_checkboxes.items()])

    def set_attribute_order(self, order_dict: typ.Dict[int, typ.List[str]]):
        # Iterate over enabled attributes only
        for attribute_id in [i for (i, e) in self.__order_enabled.items() if e]:
            # On enabling ordinal, no attribute_order list is created, so we have
            # to check first if we have to do anything at all
            if attribute_id in order_dict:
                attribute_name = self.__metadata.get_attribute_names()[attribute_id]
                self.__metadata.order_categories(attribute_name, order_dict[attribute_id])

    def toggle_order_enabled(self, id_: int, enabled: bool):
        self.__order_enabled[id_] = enabled
        attribute_name = self.__metadata.get_attribute_names()[id_]
        if enabled:
            # Set ordinal by ordering in default order
            self.__metadata.order_categories(attribute_name, self.__metadata.get_categories(attribute_name))
        else:
            self.__metadata.remove_order(attribute_name)

    def build_on_toggle_upload_type(self) -> typ.Callable:
        # TODO: What is the type of change? Dictionary?
        def on_toogle_upload_type(change):
            # Switch to network upload option
            if change['new'] == 'Network':
                # Hide local upload widgets
                self.__local_graph_file_upload.layout.display = 'none'
                self.__local_metadata_file_upload.layout.display = 'none'
                # Show network upload widgets
                self.__network_graph_upload_button.layout.display = 'inline'
                self.__network_metadata_upload_button.layout.display = 'inline'
                # Enable text input for URLs
                self.__graph_data_text.disabled = False
                self.__graph_data_text.placeholder = UIDataUploadManager.NETWORK_UPLOAD_PLACEHOLDER
                self.__metadata_data_text.disabled = False
                self.__metadata_data_text.placeholder = UIDataUploadManager.NETWORK_UPLOAD_PLACEHOLDER
            # Switch to local upload option
            else:
                # Show local upload widgets
                self.__local_graph_file_upload.layout.display = 'inline'
                self.__local_metadata_file_upload.layout.display = 'inline'
                # Hide network upload widgets
                self.__network_graph_upload_button.layout.display = 'none'
                self.__network_metadata_upload_button.layout.display = 'none'
                # Disable text input for local upload
                self.__graph_data_text.disabled = True
                self.__graph_data_text.placeholder = UIDataUploadManager.LOCAL_UPLOAD_PLACEHOLDER
                self.__metadata_data_text.disabled = True
                self.__metadata_data_text.placeholder = UIDataUploadManager.LOCAL_UPLOAD_PLACEHOLDER

        return on_toogle_upload_type

    def build_handle_upload_graph_data(self, upload_origin: UploadOrigin) -> \
            typ.Callable:
        def handle_local_upload_graph_data(change):
            # Hide widgets and output in case this is a reupload
            self.__graph_data__configuration_vbox.children = []
            with self.__graph_data_output:
                ipydisplay.clear_output()
            # TODO: What does the w stand for?
            w = change['owner'] if upload_origin is self.UploadOrigin.LOCAL \
                else None
            self.__graph_data_loading.start()
            try:
                if upload_origin is self.UploadOrigin.LOCAL:
                    file = w.filename
                    # Upload and store file to notebook directory
                    with open(UIDataUploadManager.UPLOAD_DIR + w.filename, 'wb') as f:
                        f.write(w.data)
                        self.__graph_data_text.value = w.filename
                    # Import graph as edge list via vtna
                    self.__edge_list = vtna.data_import.read_edge_table(
                        UIDataUploadManager.UPLOAD_DIR + w.filename)
                elif upload_origin is self.UploadOrigin.NETWORK:
                    file = self.__graph_data_text.value
                    self.__edge_list = vtna.data_import.read_edge_table(file)
                # Display UI for graph config
                self.__open_graph_config()
                self.__display_graph_upload_summary()
            except FileNotFoundError:
                error_msg = f'File {file} does not exist'
                self.display_graph_upload_error(error_msg)
            except (urllib.error.HTTPError, urllib.error.URLError):
                error_msg = f'Could not access URL {file}'
                self.display_graph_upload_error(error_msg)
            except OSError:
                error_msg = f'Error accessing {file}.'
                if upload_origin is self.UploadOrigin.NETWORK:
                    error_msg += " Check your internet access or try again later."
                self.display_graph_upload_error(error_msg)
            except ValueError:
                error_msg = f'Invalid format: Columns 1-3 in {file} must be integers'
                self.display_graph_upload_error(error_msg)
            finally:
                self.__graph_data_loading.stop()

        return handle_local_upload_graph_data

    def build_handle_upload_metadata(self, upload_origin: UploadOrigin) -> typ.Callable:
        def handle_local_upload_metadata(change):
            # Hide widgets and output in case this is a reupload
            self.__metadata_configuration_vbox.children = []
            with self.__metadata_data_output:
                ipydisplay.clear_output()
            w = change['owner'] if upload_origin is self.UploadOrigin.LOCAL \
                else None
            self.__metadata_loading.start()
            try:
                if upload_origin is self.UploadOrigin.LOCAL:
                    file = w.filename
                    with open(UIDataUploadManager.UPLOAD_DIR + w.filename, 'wb') as f:
                        f.write(w.data)
                        self.__metadata_data_text.value = w.filename
                    # Load metadata
                    self.__metadata = vtna.data_import.MetadataTable(UIDataUploadManager.UPLOAD_DIR + w.filename)
                elif upload_origin is self.UploadOrigin.NETWORK:
                    file = self.__metadata_data_text.value
                    self.__metadata = vtna.data_import.MetadataTable(file)
                # Initialize orders as disabled
                self.__order_enabled = dict([(i, False) for i in range(len(self.__metadata.get_attribute_names()))])
                self.__metadata_loading.stop()
                self.__display_metadata_upload_summary()
                # Display UI for configuring metadata
                self.__open_column_config()
            except FileNotFoundError:
                error_msg = f'File {file} does not exist'
                self.display_metadata_upload_error(error_msg)
            except (urllib.error.HTTPError, urllib.error.URLError):
                error_msg = f'Could not access URL {file}'
                self.display_metadata_upload_error(error_msg)
            except OSError:
                error_msg = f'Error accessing {file}.'
                if upload_origin is self.UploadOrigin.NETWORK:
                    error_msg += " Check your internet access or try again later."
                self.display_graph_upload_error(error_msg)
            except ValueError:
                error_msg = f'Invalid format: Column 1 in {file} must be integer'
                self.display_metadata_upload_error(error_msg)
            finally:
                self.__metadata_loading.stop()

        return handle_local_upload_metadata

    def display_graph_upload_error(self, msg: str):
        with self.__graph_data_output:
            ipydisplay.clear_output()
            print(f'\x1b[31m{msg}\x1b[0m')
        with self.__graph_hist_output:
            ipydisplay.clear_output()

    def __display_graph_upload_summary(self, prepend_msgs: typ.List[str] = None):
        self.__graph_data_loading.start()
        self.__graph_data_output.layout.display = 'none'
        self.__graph_hist_output.layout.display = 'none'
        with self.__graph_data_output:
            ipydisplay.clear_output()
            if prepend_msgs is not None:
                for msg in prepend_msgs:
                    print(msg)
            print_edge_stats(self.__edge_list)
        # Collect/Generate data for edge histogram plot
        earliest, _ = vtna.data_import.get_time_interval_of_edges(self.__edge_list)
        granularity = self.__granularity
        title = f'Interactions in interval bins of length {granularity} seconds'
        histogram = vtna.statistics.histogram_edges(self.__edge_list, granularity)
        x = list(range(len(histogram)))
        with self.__graph_hist_output:
            ipydisplay.clear_output()
            # Plot edge histogram
            plt.figure(figsize=(14, 4))
            _ = plt.bar(list(range(len(histogram))), histogram)
            plt.title(title)
            plt.xlabel(f'Time intervals of length {granularity} seconds')
            plt.ylabel('Number of interactions')
            plt.xticks(x, [''] * len(x))
            plt.show()
        self.__graph_data_output.layout.display = 'block'
        self.__graph_hist_output.layout.display = 'block'
        self.__graph_data_loading.stop()

    def display_metadata_upload_error(self, msg):
        with self.__metadata_data_output:
            ipydisplay.clear_output()
            print(f'\x1b[31m{msg}\x1b[0m')

    def __display_metadata_upload_summary(self, prepend_msgs: typ.List[str] = None):
        with self.__metadata_data_output:
            ipydisplay.clear_output()
            if prepend_msgs is not None:
                for msg in prepend_msgs:
                    print(msg)
            table = ipydisplay.HTML(create_html_metadata_summary(self.__metadata, self.__order_enabled))
            ipydisplay.display_html(table)  # A tuple is expected as input, but then it won't work for some reason...

    def __open_column_config(self):
        """
        Shows menu to configure metadata.
        Currently only supports setting of names.
        Changes are made to Text widgets in w_attribute_settings.children
        """
        # Hide widgets in case this is a reupload
        self.__metadata_configuration_vbox.children = []

        current_names = sorted(self.__metadata.get_attribute_names())
        column_text_fields = list()  # type: typ.List[widgets.Text]

        # Load some default settings
        for name in current_names:
            w_col_name = widgets.Text(
                value='{}'.format(name),
                placeholder='New Column Name',
                description=f'Column {name}:',
                disabled=False
            )
            column_text_fields.append(w_col_name)
            self.__metadata_configuration_vbox.children += \
                (widgets.VBox([w_col_name], layout=self.__column_configuration_layout),)

        rename_button = widgets.Button(
            description='Rename',
            disabled=False,
            button_style='primary',
            tooltip='Renames columns',
        )
        rename_hbox = widgets.HBox(layout=widgets.Layout(justify_content='flex-end'))
        rename_hbox.children = [rename_button]
        self.__metadata_configuration_vbox.children += (rename_hbox,)

        self.__metadata_ordinal_help.layout.display = 'block'

        def apply_rename(_):
            to_rename = dict()
            for i in range(len(current_names)):
                if current_names[i] != column_text_fields[i].value:
                    to_rename[current_names[i]] = column_text_fields[i].value
            msgs = list()
            try:
                self.__metadata.rename_attributes(to_rename)
                for i, new_name in enumerate(map(lambda f: f.value, column_text_fields)):
                    current_names[i] = new_name
            except vtna.data_import.DuplicateTargetNamesError as e:
                msgs.append(f'\x1b[31mRenaming failed: {", ".join(e.illegal_names)} are duplicates\x1b[0m')
            except vtna.data_import.RenamingTargetExistsError as e:
                msgs.append(f'\x1b[31mRenaming failed: {", ".join(e.illegal_names)} already exist\x1b[0m')
            self.__display_metadata_upload_summary(prepend_msgs=msgs)

        rename_button.on_click(apply_rename)

    def __open_graph_config(self):
        earliest, latest = vtna.data_import.get_time_interval_of_edges(self.__edge_list)
        update_delta = vtna.data_import.infer_update_delta(self.__edge_list)
        self.__granularity = update_delta * 100

        # Maps time unit strings to corresponding length in seconds
        time_unit_dict = {
            'seconds': 1,
            'minutes': 60,
            'hours': 60*60,
            'days': 60*60*24
        }

        self.__run_button.disabled = False

        granularity_bounded_int_text = widgets.BoundedIntText(
            description='Interval length:',
            value=self.__granularity,
            min=update_delta,
            max=latest - earliest,
            step=update_delta,
            layout=widgets.Layout(width="15em"),
            disabled=False
        )
        granularity_unit_dropdown = widgets.Dropdown(
            options=time_unit_dict,
            value=time_unit_dict['seconds'],
            layout=widgets.Layout(width="10em")
        )
        apply_granularity_button = widgets.Button(
            description='Apply',
            disabled=False,
            button_style='primary',
            tooltip='Apply selected granularity on graph',
        )

        def update_granularity_step(change):
            if change['type'] == 'change' and change['name'] == 'value':
                if granularity_unit_dropdown.value == time_unit_dict['seconds']:
                    granularity_bounded_int_text.min = update_delta
                    granularity_bounded_int_text.max = latest - earliest
                    granularity_bounded_int_text.step = update_delta
                    granularity_bounded_int_text.value = update_delta * 100
                else:
                    # TODO: Minimum and step should be dependent on update_delta
                    # The problem is that an update_delta of e.g. 100 seconds would
                    # result in a step of 1.6666... for minutes
                    granularity_bounded_int_text.min = 1
                    granularity_bounded_int_text.max = (latest - earliest) / granularity_unit_dropdown.value
                    granularity_bounded_int_text.step = 1
                    granularity_bounded_int_text.value = 1

        granularity_unit_dropdown.observe(update_granularity_step)

        def update_granularity_and_graph_data_output(_):
            apply_granularity_button.disabled = True
            old_name = apply_granularity_button.description
            apply_granularity_button.description = 'Loading...'

            new_granularity = granularity_bounded_int_text.value * granularity_unit_dropdown.value

            extra_msgs = []
            if (new_granularity < update_delta or
                    new_granularity > latest - earliest or
                    new_granularity % update_delta != 0):
                error_msg = f'\x1b[31m{granularity_bounded_int_text.value} is an invalid granularity\x1b[0m'
                extra_msgs.append(error_msg)
            else:
                self.__granularity = new_granularity
                self.__run_button.disabled = False
            self.__display_graph_upload_summary(prepend_msgs=extra_msgs)

            apply_granularity_button.description = old_name
            apply_granularity_button.disabled = False

        apply_granularity_button.on_click(update_granularity_and_graph_data_output)

        granularity_help = help_widget(HELP_TEXT['granularity'])

        self.__graph_data__configuration_vbox.children = \
            [widgets.HBox([granularity_bounded_int_text, granularity_unit_dropdown, granularity_help,
                           apply_granularity_button])]

    def __display_measure_selection(self, container_box: widgets.Box):
        # Reset internal widget dict
        self.__measure_selection_checkboxes = {}
        header = widgets.HTML("<h3>Available Measures:</h3>")
        vbox_layout = widgets.Layout(align_content="center")
        local_checkboxes_vbox = widgets.VBox([widgets.HTML('<b>Local</b>')], layout=vbox_layout)
        global_checkboxes_vbox = widgets.VBox([widgets.HTML('<b>Global</b>')], layout=vbox_layout)
        measure_names_vbox = widgets.VBox([widgets.HTML('<b>Name</b>')], layout=vbox_layout)
        checkbox_layout = widgets.Layout(width="3em", margin="2px 1em 2px 0")
        for index in range(len(NodeMeasuresManager.node_measure_classes) // 2):
            # Get measure names from static dict keys
            local_measure_name = list(NodeMeasuresManager.node_measure_classes.keys())[index * 2]
            global_measure_name = list(NodeMeasuresManager.node_measure_classes.keys())[index * 2 + 1]
            measure_name = local_measure_name.replace("Local ", "")
            # Add checkbox for local measure
            local_checkbox = widgets.Checkbox(layout=checkbox_layout)
            self.__measure_selection_checkboxes[local_measure_name] = local_checkbox
            local_checkboxes_vbox.children += local_checkbox,
            # Add checkbox for global measure
            global_checkbox = widgets.Checkbox(layout=checkbox_layout)
            self.__measure_selection_checkboxes[global_measure_name] = global_checkbox
            global_checkboxes_vbox.children += global_checkbox,
            # Add measure name
            measure_names_vbox.children += widgets.Label(value=measure_name),
        container_box.children = [
            widgets.HBox([header, help_widget(HELP_TEXT['measures_selection'], style='padding-top: 1.75em;')]),
            widgets.HBox([local_checkboxes_vbox, global_checkboxes_vbox, measure_names_vbox])
        ]


def print_edge_stats(edges: typ.List[vtna.data_import.TemporalEdge]):
    print('Total Edges:', len(edges))
    print('Inferred Update Interval:', vtna.data_import.infer_update_delta(edges), 'seconds')
    interval = vtna.data_import.get_time_interval_of_edges(edges)
    print('Total Dataset Time:', str(datetime.timedelta(seconds=(interval[1]-interval[0]))), 'hours')


def create_html_metadata_summary(metadata: vtna.data_import.MetadataTable, order_enabled: typ.Dict[int, bool]) -> str:
    col_names = metadata.get_attribute_names()
    categories = [metadata.get_categories(name) for name in col_names]

    # table header with attribute/column names
    header_html = ""
    # Checkbox for toggling ordinal
    checkbox_html = """
        <label><input type="checkbox" value="{value}" onchange="toggleSortable(this)" {checked}> Ordinal</label>"""
    for i, col_name in enumerate(col_names):
        # Create table header plus checkbox for ordering
        header_html += f'<th>{col_name}<br>{checkbox_html.format(value=i, checked="checked" if order_enabled[i] else "")}</th>'
    header_html = f'<tr>{header_html}</tr>'

    # Contains all attribute lists
    body_html = ""
    for category in categories:
        list_width = max(map(len, category))
        # list of lis for every attribute category
        # TODO: Make width work to prevent resizing on D&D
        li_list = [f'<li width="{list_width}em">{category[element_id]}</li>' for element_id in
                   range(len(category))]
        # ul element of a category, attrlist class is for general styling, id is needed for
        # attaching the sortable js listener
        ul = '<ul class="attrlist{sortable}" id="attr_list{id}">{lis}</ul>'
        cat_id = categories.index(category)
        ul = ul.format(sortable=" sortlist" if order_enabled[cat_id] else "", id=cat_id, lis=''.join(li_list))
        # Surround with td that aligns text at the top, otherwise it would be centered
        ul = f'<td style="vertical-align:top">{ul}</td>'
        body_html += ul
    # nohover css style prevents blue background on mouse hover event
    body_html = f'<tr id="nohover">{body_html}</tr>'

    table_html = f"""
        <table>
            {header_html}
            {body_html}
        </table>
    """
    return table_html


class UIGraphDisplayManager(object):
    DEFAULT_UPDATE_DELTA = 20
    DEFAULT_LAYOUT_IDX = 0
    LAYOUT_FUNCTIONS = [
        vtna.layout.static_spring_layout,
        vtna.layout.flexible_spring_layout,
        vtna.layout.static_weighted_spring_layout,
        vtna.layout.flexible_weighted_spring_layout,
        vtna.layout.chained_weighted_spring_layout,
        vtna.layout.random_walk_pca_layout
    ]

    def __init__(self,
                 display_output: widgets.Output,
                 display_size: typ.Tuple[int, int],
                 layout_vbox: widgets.VBox,
                 export_vbox: widgets.VBox,
                 cumulative_hbox: widgets.HBox,
                 loading_indicator: 'LoadingIndicator',
                 style_manager: 'UIDefaultStyleOptionsManager'
                 ):
        self.__display_output = display_output
        self.__display_size = display_size

        self.__style_manager = style_manager
        self.__style_manager.register_graph_display_manager(self)

        # queries_manager has to be added later.
        self.__queries_manager = None  # type: UIAttributeQueriesManager

        self.__layout_vbox = layout_vbox
        self.__export_vbox = export_vbox
        self.__cumulative_hbox = cumulative_hbox
        self.__loading_indicator = loading_indicator

        self.__cumulative_checkbox = None

        self.__temp_graph = None  # type: vtna.graph.TemporalGraph
        self.__update_delta = UIGraphDisplayManager.DEFAULT_UPDATE_DELTA  # type: int
        self.__granularity = None  # type: int

        self.__layout_function = UIGraphDisplayManager.LAYOUT_FUNCTIONS[UIGraphDisplayManager.DEFAULT_LAYOUT_IDX]

        self.__node_measure_manager = None  # type: NodeMeasuresManager

        self.__figure = None  # type: TemporalGraphFigure
        self.__video_export_manager = None  # type: VideoExport

        self.__init_layout_selection_widgets()
        self.__init_export_widgets()

    def __init_layout_selection_widgets(self):
        layout_options = dict((func.name, func) for func in UIGraphDisplayManager.LAYOUT_FUNCTIONS)
        # Calulate widget width for dropdown selection box and slider widgets
        widget_width = f'{max(map(len, layout_options.keys())) + 1}rem'
        self.__layout_select = widgets.Dropdown(
            options=layout_options,
            value=UIGraphDisplayManager.LAYOUT_FUNCTIONS[UIGraphDisplayManager.DEFAULT_LAYOUT_IDX],
            description='Layout:',
            # Width of dropdown is based on maximal length of function name.
            layout=widgets.Layout(width=widget_width)
        )
        self.__layout_select.observe(self.__build_select_layout())

        # left padding so the right align of the drop down label is simulated, I cannot get text-align to work,
        # so this is the alternative approach.
        self.__layout_description_output = widgets.Output()
        self.__display_layout_description()

        parameter_widget_layout = widgets.Layout(width=widget_width)

        # Hyperparameters of basic layouts
        self.__layout_parameter_nodedistance_slider = widgets.FloatSlider(
            description='Repel:',
            value=1.0,
            min=0.1,
            max=100,
            layout=parameter_widget_layout
        )
        self.__layout_parameter_iterations_slider = widgets.IntSlider(
            description='Iterations:',
            value=50,
            min=1,
            max=500,
            layout=parameter_widget_layout
        )

        # Hyperparameters of PCA layout
        self.__layout_parameter_PCA_n_slider = widgets.IntSlider(
            description='n:',
            value=25,
            min=1,
            max=300,
            layout=parameter_widget_layout
        )
        self.__layout_parameter_PCA_repel_slider = widgets.FloatSlider(
            description='Repel:',
            value=1.0,
            min=0.1,
            max=100,
            layout=parameter_widget_layout
        )

        self.__apply_layout_button = widgets.Button(
            description='Apply',
            disabled=False,
            button_style='primary',
            tooltip='Apply Layout',
        )
        self.__apply_layout_button.on_click(self.__build_apply_layout())
        self.__set_current_layout_widgets()

    def __init_export_widgets(self):
        self.__export_format_dropdown = widgets.Dropdown(
            options={'GIF': 'gif', 'MP4': 'mp4', 'MOV': 'mov', 'AVI': 'avi'},
            value='gif',
            description='Format:',
        )
        self.__export_resolution = widgets.BoundedIntText(
            min=400,
            max=10000,
            value=500,
            description='Size:'
        )
        self.__export_frame_length_text = widgets.BoundedIntText(
            value=500,
            min=10,
            max=10000,
            step=10,
            description='Frame length:',
            disabled=False
        )
        self.__export_range_slider = widgets.SelectionRangeSlider(
            options=[0],
            description='Time range:',
            display='none',
            orientation='horizontal',
            layout=widgets.Layout(width="90%")
        )
        self.__export_speedup_empty_frames_checkbox = widgets.Checkbox(
            value=False,
            description='Speed up empty frames',
            disabled=False
        )
        self.__export_speedup_warning = widgets.HTML(
            value='<span style="color:#FF3A19">Warning: Speed up will be limited because frame length is too short</span>',
            layout=widgets.Layout(display='none')
        )
        self.__download_button = widgets.Button(
            description='Export animation',
            disabled=False,
            button_style='primary',
            tooltip='Download a video of the animated plot',
        )
        self.__export_progressbar = widgets.IntProgress(
            value=0,
            min=0,
            step=1,
            bar_style='success',
            orientation='horizontal',
            layout=widgets.Layout(display='none')
        )
        self.__export_format_dropdown.observe(self.__build_configure_export())
        self.__export_frame_length_text.observe(self.__build_configure_export())
        self.__export_speedup_empty_frames_checkbox.observe(self.__build_configure_export())
        self.__download_button.on_click(self.__build_export_video())
        self.__export_vbox.children = [
            self.__export_format_dropdown,
            widgets.HBox([self.__export_resolution, widgets.Label("pixels")]),
            widgets.HBox([self.__export_frame_length_text, widgets.Label(value="ms")]),
            self.__export_range_slider,
            widgets.HBox([self.__export_speedup_empty_frames_checkbox, self.__export_speedup_warning]),
            widgets.HBox([self.__download_button, self.__export_progressbar])
        ]

    def init_temporal_graph(self,
                            edge_list: typ.List[vtna.data_import.TemporalEdge],
                            metadata: vtna.data_import.MetadataTable,
                            granularity: int,
                            selected_measures: typ.Dict[str, bool],
                            ):
        self.__temp_graph = vtna.graph.TemporalGraph(edge_list, metadata, granularity)
        layout = self.__compute_layout()

        self.__node_measure_manager = NodeMeasuresManager(self.__temp_graph,
                                                          [m for m, selected in selected_measures.items() if selected])
        self.__node_measure_manager.add_all_to_graph()

        self.__figure = TemporalGraphFigure(temp_graph=self.__temp_graph,
                                            layout=layout,
                                            display_size=self.__display_size,
                                            animate_transitions=not self.__layout_function.is_static,
                                            color_map=self.__style_manager.get_node_color(),
                                            edge_color=self.__style_manager.get_edge_color(),
                                            node_size=self.__style_manager.get_node_size(),
                                            edge_width=self.__style_manager.get_edge_width()
                                            )
        self.__update_delta = vtna.data_import.infer_update_delta(edge_list)

        # Set options for time range slider of export and make it visible
        options = [' ' + str(datetime.timedelta(seconds=timestep * self.__temp_graph.get_granularity())) + ' hours ' for timestep in range(len(self.__temp_graph))]
        self.__export_range_slider.options = options
        self.__export_range_slider.layout.display = 'inline-flex'
        self.__export_range_slider.index = (0, len(self.__temp_graph)-1)

        self.__init_cumulative_option_widgets()

    def __init_cumulative_option_widgets(self):
        self.__cumulative_checkbox = widgets.Checkbox(
            value=False,
            description='Cumulative Graphs',
            disabled=False,
        )
        self.__cumulative_checkbox.observe(self.__build_change_cumulative())
        self.__cumulative_hbox.children = [self.__cumulative_checkbox]

    def init_queries_manager(self, queries_manager: 'UIAttributeQueriesManager'):
        """Initializies the Query Manager."""
        self.__queries_manager = queries_manager
        self.__queries_manager.register_graph_display_manager(self)

    def display_graph(self):
        plot_div_html = plotly.offline.plot(self.__figure.get_figure(), include_plotlyjs=False,
                                            config={'scrollZoom': True, 'modeBarButtonsToRemove': ['sendDataToCloud'],},
                                            show_link=False, output_type='div')
        # Remove js code that would cause autoplay
        plot_div_html = re.sub("\\.then\\(function\\(\\)\\{Plotly\\.animate\\(\\'[0-9a-zA-Z-]*\\'\\)\\;\\}\\)", "",
                               plot_div_html)
        with self.__display_output:
            ipydisplay.clear_output()
            ipydisplay.display(ipydisplay.HTML(plot_div_html))

    def get_temporal_graph(self) -> vtna.graph.TemporalGraph:
        return self.__temp_graph

    def notify(self, observable) -> None:
        if isinstance(observable, UIAttributeQueriesManager):
            # => Call from QueryManager class
            self.__start_graph_loading()

            node_filter = observable.get_node_filter()
            self.__figure.update_filter(node_filter)
            node_colors = observable.get_node_colors(self.__temp_graph, self.__style_manager.get_node_color())
            self.__figure.update_colors(node_colors)
            self.display_graph()
            self.__stop_graph_loading()
        elif isinstance(observable, UIDefaultStyleOptionsManager):
            self.__start_graph_loading()
            node_colors = self.__queries_manager.get_node_colors(self.__temp_graph, observable.get_node_color())
            edge_color = self.__style_manager.get_edge_color()
            node_size = self.__style_manager.get_node_size()
            edge_width = self.__style_manager.get_edge_width()
            frame_length = self.__style_manager.get_animation_frame_length()
            self.__figure.update_colors(node_colors)
            self.__figure.update_edge_color(edge_color)
            self.__figure.update_node_size(node_size)
            self.__figure.update_edge_width(edge_width)
            self.__figure.update_animation_frame_length(frame_length)
            self.display_graph()
            self.__stop_graph_loading()

    def __display_layout_description(self):
        description_html = '<p style="color: blue;">{}</p>'.format(self.__layout_select.value.description)

        with self.__layout_description_output:
            ipydisplay.clear_output()
            ipydisplay.display_html(ipydisplay.HTML(description_html))

    def __build_apply_layout(self):
        def apply_layout(_):
            # Disable button and change name to Loading...
            self.__apply_layout_button.disabled = True
            old_button_name = self.__apply_layout_button.description
            self.__apply_layout_button.description = 'Loading...'
            self.__start_graph_loading()
            # Compute layout...
            self.__layout_function = self.__layout_select.value
            layout = self.__compute_layout()
            # ... and update figure
            self.__figure.toggle_animate_transitions(not self.__layout_function.is_static)
            self.__figure.update_layout(layout)
            # Enable button, restore old name
            self.__apply_layout_button.description = old_button_name
            self.__apply_layout_button.disabled = False
            # Update graph display
            self.display_graph()
            self.__stop_graph_loading()

        return apply_layout

    def __build_select_layout(self) -> typ.Callable:
        def select_layout(change):
            if change['type'] == 'change' and change['name'] == 'value':
                self.__apply_layout_button.disabled = True
                self.__layout_select.disabled = True
                self.__display_layout_description()
                # Set widget layout for parameters of new layout
                self.__set_current_layout_widgets()
                self.__layout_select.disabled = False
                self.__apply_layout_button.disabled = False

        return select_layout

    def __build_configure_export(self) -> typ.Callable:
        def on_configure_export(change):
            if change['type'] == 'change' and change['name'] == 'value':
                # If user wants a sped up gif and
                # sped up frame length is smaller than minimum of 10ms
                if self.__export_format_dropdown.value == 'gif' and \
                        self.__export_frame_length_text.value / 10 < 10 and \
                        self.__export_speedup_empty_frames_checkbox.value:
                    self.__export_speedup_warning.layout.display = 'inline-flex'
                else:
                    self.__export_speedup_warning.layout.display = 'none'
                # Speeding up on empty frames is only possible for gifs right now
                if self.__export_format_dropdown.value == 'gif':
                    self.__export_speedup_empty_frames_checkbox.layout.display = 'inline-flex'
                else:
                    self.__export_speedup_empty_frames_checkbox.layout.display = 'none'

        return on_configure_export

    def __compute_layout(self):
        """Returns layout dependent on selected layout and hyperparameters"""
        # Read out parameters of widgets, dependent on selected layout
        if self.__layout_select.value in [
            vtna.layout.static_spring_layout,
            vtna.layout.flexible_spring_layout,
            vtna.layout.static_weighted_spring_layout,
            vtna.layout.flexible_weighted_spring_layout,
            vtna.layout.chained_weighted_spring_layout
        ]:
            return self.__layout_function(
                temp_graph=self.__temp_graph,
                node_distance_scale=self.__layout_parameter_nodedistance_slider.value,
                n_iterations=self.__layout_parameter_iterations_slider.value
            )
        elif self.__layout_select.value in [
            vtna.layout.random_walk_pca_layout
        ]:
            return self.__layout_function(
                temp_graph=self.__temp_graph,
                n=self.__layout_parameter_PCA_n_slider.value,
                repel=self.__layout_parameter_PCA_repel_slider.value
            )

    def __set_current_layout_widgets(self):
        """Generates list of widgets for layout_vbox.children"""
        widget_list = list()
        widget_list.append(self.__layout_select)
        if self.__layout_select.value in [
            vtna.layout.static_spring_layout,
            vtna.layout.flexible_spring_layout,
            vtna.layout.static_weighted_spring_layout,
            vtna.layout.flexible_weighted_spring_layout,
            vtna.layout.chained_weighted_spring_layout
        ]:
            widget_list.extend([
                self.__layout_parameter_nodedistance_slider,
                self.__layout_parameter_iterations_slider
            ])
        elif self.__layout_select.value in [
            vtna.layout.random_walk_pca_layout
        ]:
            widget_list.extend([
                self.__layout_parameter_PCA_n_slider,
                self.__layout_parameter_PCA_repel_slider
            ])
        widget_list.extend([self.__layout_description_output, self.__apply_layout_button])
        self.__layout_vbox.children = widget_list

    def __build_export_video(self) -> typ.Callable:
        def initialize_progressbar(steps):
            """Callback for setting max amount of progress steps and showing the progress bar"""
            self.__export_progressbar.description = 'Exporting:'
            self.__export_progressbar.max = steps
            self.__export_progressbar.value = 0
            self.__export_progressbar.layout.display = 'inline-flex'

        def increment_progress():
            """Callback for incrementing progress by 1"""
            self.__export_progressbar.value += 1

        def progress_finished():
            """Callback after progress is done. Shows text and hides the progress bar"""
            self.__export_progressbar.description = 'Finished!'
            # Unlock buttons
            self.__apply_layout_button.disabled = False
            self.__queries_manager.get_apply_button().disabled = False
            self.__style_manager.get_apply_button().disabled = False
            self.__download_button.disabled = False
            # Open file in browser
            js_output = widgets.Output()
            ipydisplay.display(js_output)
            with js_output:
                output_path = self.__video_export_manager.get_output_path()
                ipydisplay.display(ipydisplay.Javascript(f"""
                var to = window.location.href.lastIndexOf('/') +1;
                window.open(window.location.href.substring(0,to)+'{output_path}', '_blank');
                """))
            # Hide progress bar after 5 seconds
            threading.Timer(5.0, __hide_progressbar).start()

        def __hide_progressbar():
            self.__export_progressbar.layout.display = 'none'

        def export_video(_):
            # Lock buttons
            self.__apply_layout_button.disabled = True
            self.__queries_manager.get_apply_button().disabled = True
            self.__style_manager.get_apply_button().disabled = True
            self.__download_button.disabled = True
            # Start export
            self.__video_export_manager = VideoExport(
                figure=self.__figure.get_figure(),
                video_format=self.__export_format_dropdown.value,
                video_resolution=self.__export_resolution.value,
                frame_length=self.__export_frame_length_text.value,
                time_range=self.__export_range_slider.index,
                speedup_empty_frames=self.__export_speedup_empty_frames_checkbox.value,
                initialize_progressbar=initialize_progressbar,
                increment_progress=increment_progress,
                progress_finished=progress_finished)

        return export_video

    # This is just a propagation method so the JS code/the notebook can access
    # the non-static export manager.
    def write_export_frame(self, img):
        return self.__video_export_manager.write_frame(img)

    def __start_graph_loading(self):
        self.__loading_indicator.start()
        with self.__display_output:
            ipydisplay.clear_output()

    def __stop_graph_loading(self):
        self.__loading_indicator.stop()

    def __build_change_cumulative(self) -> typ.Callable:
        def on_change(change):
            if change['type'] == 'change' and change['name'] == 'value':
                self.__start_graph_loading()
                self.__cumulative_checkbox.disabled = True
                self.__temp_graph.set_cumulative(self.__cumulative_checkbox.value)
                layout = self.__compute_layout()
                self.__figure.update_layout(layout)
                self.display_graph()
                self.__cumulative_checkbox.disabled = False
                self.__stop_graph_loading()
        return on_change


class UIAttributeQueriesManager(object):
    RELEVANT_NODE_DISPLAY_LIMIT = 5

    def __init__(self,
                 temp_graph: vtna.graph.TemporalGraph,
                 queries_main_vbox: widgets.VBox,
                 filter_box_layout: widgets.Layout,
                 query_html_template_path: str,
                 relevant_node_html_template_path: str):
        self.__queries_main_vbox = queries_main_vbox
        self.__filter_box_layout = filter_box_layout
        self.__temp_graph = temp_graph
        self.__attribute_info = temp_graph.get_attributes_info()
        self.__attribute_info['Node ID'] = {
            'measurement_type': 'ID',
            'scope': 'global',
            'ids': [node.get_id() for node in temp_graph.get_nodes()]
        }

        with open(query_html_template_path, mode='rt') as f:
            self.__query_template = f.read()

        with open(relevant_node_html_template_path, mode='rt') as f:
            self.__relevant_node_template = f.read()

        self.__filter_highlight_toggle_buttons = None  # type: widgets.ToggleButtons

        self.__attributes_dropdown = None  # type: widgets.Dropdown
        self.__node_id_int_text = None  # type: widgets.IntText
        self.__nominal_value_dropdown = None  # type: widgets.Dropdown
        self.__interval_value_float_slider = None  # type: widgets.Dropdown
        self.__ordinal_value_selection_range_slider = None  # type: widgets.Dropdown
        self.__value_error_html = None  # type: widgets.HTML

        self.__color_picker = None  # type: widgets.ColorPicker
        self.__color_picker_msg_html = None  # type: widgets.HTML

        self.__add_new_query_button = None  # type: widgets.Button
        self.__add_new_neg_query_button = None  # type: widgets.Button
        self.__add_new_clause_msg_html = None  # type: widgets.HTML
        self.__queries_help_button = None  # type: widgets.HTML
        self.__queries_output_box = None  # type: widgets.Box

        self.__relevant_nodes_overview_html = None  # type: widgets.HTML

        self.__filter_query_counter = 1  # type: int
        self.__filter_queries = dict()  # type: typ.Dict
        self.__active_filter_queries = list()  # type: typ.List[int]

        self.__graph_display_managers = list()  # type: typ.List[UIGraphDisplayManager]

        self.__highlight_query_counter = 1  # type: int
        self.__highlight_queries = dict()  # type: typ.Dict
        self.__active_highlight_queries = list()  # type: typ.List[int]

        self.__build_queries_menu()

        self.__attributes_dropdown.observe(self.__build_on_attribute_change())
        self.__filter_highlight_toggle_buttons.observe(self.__build_on_mode_change())
        self.__add_new_query_button.on_click(self.__build_add_query(False))
        self.__add_new_neg_query_button.on_click(self.__build_add_query(True))
        self.__delete_all_queries_button.on_click(self.__build_delete_all_queries())

    def __build_queries_menu(self):
        attributes = list(filter(lambda a: self.__attribute_info[a]['scope'] == 'global', self.__attribute_info.keys()))
        initial_attribute = self.__attribute_info[attributes[0]]
        # Attribute drop down
        self.__attributes_dropdown = widgets.Dropdown(
            options=attributes,
            value=attributes[0],
            description='Attribute:',
            disabled=False,
        )
        # Node ID input
        self.__node_id_int_text = widgets.IntText(
            value=0,
            disabled=True if initial_attribute['measurement_type'] != 'ID' else False,
            description='Node ID:',
            tooltip='Select a specific Node by ID'
        )
        # Nominal dropdown
        self.__nominal_value_dropdown = widgets.Dropdown(
            disabled=True if initial_attribute['measurement_type'] != 'N' else False,
            options=initial_attribute['categories'] if initial_attribute['measurement_type'] == 'N' else ['Range'],
            value=initial_attribute['categories'][0] if initial_attribute['measurement_type'] == 'N' else 'Range',
            description='Value:',
        )
        # Interval slider
        self.__interval_value_float_slider = widgets.FloatRangeSlider(
            description='Value:',
            disabled=True if initial_attribute['measurement_type'] != 'I' else False,
            value=initial_attribute['range'] if initial_attribute['measurement_type'] == 'I' else (0, 0),
            min=initial_attribute['range'][0] if initial_attribute['measurement_type'] == 'I' else 0,
            max=initial_attribute['range'][1] if initial_attribute['measurement_type'] == 'I' else 1,
            step=0.1,
            orientation='horizontal',
            readout=False if initial_attribute['measurement_type'] != 'I' else True,
            readout_format='.1f',
            layout=widgets.Layout(width='99%')
        )
        # Ordinal slider
        self.__ordinal_value_selection_range_slider = widgets.SelectionRangeSlider(
            description='Value:',
            options=initial_attribute['categories'] if initial_attribute['measurement_type'] == 'O' else ['N/A'],
            index=(0, len(initial_attribute['categories']) - 1) if initial_attribute['measurement_type'] == 'O' else (0, 0),
            disabled=True if initial_attribute['measurement_type'] != 'O' else False,
            layout=widgets.Layout(width='99%')
        )
        # Colorpicker
        self.__color_picker = widgets.ColorPicker(
            concise=False,
            value='#0000FF',
            description='Color:',
            disabled=False
        )
        self.__color_picker.layout.display = 'flex-inline'
        # Add new query
        self.__add_new_query_button = widgets.Button(
            disabled=False,
            description='Create Pos. Query',
            button_style='success',
            icon='plus',
            layout=widgets.Layout(width='auto'),
            tooltip=TOOLTIP['add_query_button']
        )
        # Add new negated query
        self.__add_new_neg_query_button = widgets.Button(
            disabled=False,
            description='Create Neg. Query',
            button_style='success',
            icon='minus',
            layout=widgets.Layout(width='auto'),
            tooltip=TOOLTIP['add_neg_query_button']
        )
        # Delete all queries
        self.__delete_all_queries_button = widgets.Button(
            description='Reset',
            disabled=False,
            button_style='',
            icon='refresh',
            layout=widgets.Layout(width='auto')
        )
        # switch between Filter mode or Highlight mode
        self.__filter_highlight_toggle_buttons = widgets.ToggleButtons(
            options=['Filter', 'Highlight'],
            description='',
            value='Highlight',
            button_style=''
        )

        # display inputs depending on current initial data type
        if initial_attribute['measurement_type'] == 'O':
            self.__nominal_value_dropdown.layout.display = 'none'
            self.__interval_value_float_slider.layout.display = 'none'
            self.__node_id_int_text.layout.display = 'none'
        elif initial_attribute['measurement_type'] == 'I':
            self.__nominal_value_dropdown.layout.display = 'none'
            self.__ordinal_value_selection_range_slider.layout.display = 'none'
            self.__node_id_int_text.layout.display = 'none'
        elif initial_attribute['measurement_type'] == 'N':
            self.__interval_value_float_slider.layout.display = 'none'
            self.__ordinal_value_selection_range_slider.layout.display = 'none'
            self.__node_id_int_text.layout.display = 'none'
        else:
            self.__interval_value_float_slider.layout.display = 'none'
            self.__ordinal_value_selection_range_slider.layout.display = 'none'
            self.__nominal_value_dropdown.layout.display = 'none'

        # Msg for attribute values
        self.__value_error_html = widgets.HTML('')
        self.__value_error_html.layout.display = 'none'
        # Msg for colorpicker
        self.__color_picker_msg_html = widgets.HTML(
            value="<span style='color:#7f8c8d'> Click <i style='color:#9b59b6;' class='fa fa-paint-brush'></i> "
                  "to change highlight color</span>")
        self.__color_picker.layout.display = 'inline-flex'
        # Msg for add new clause
        self.__add_new_clause_msg_html = widgets.HTML(
            value="<span style='color:#7f8c8d'> Use the <i style='color:#2ecc71;' class='fa fa-plus-square'></i> "
                  "to add a clause to a query</span>")
        self.__queries_help_button = help_widget(HELP_TEXT['queries'])
        self.__relevant_nodes_overview_html = widgets.HTML('', layout=widgets.Layout(height='8em'))
        self.__relevant_nodes_accordion = widgets.Accordion([self.__relevant_nodes_overview_html])
        self.__relevant_nodes_accordion.set_title(0, 'Overview: Queried Nodes')
        # Collapses all accordion windows
        self.__relevant_nodes_accordion.selected_index = None

        # Apply queries to graph button
        self.__apply_to_graph_button = widgets.Button(
            description='Apply',
            disabled=False,
            button_style='primary',
            tooltip=TOOLTIP['apply_queries_to_graph_button'],
        )
        self.__apply_to_graph_button.on_click(lambda _: self.__notify_all())

        # Queries toolbar: Reset(delete all), toggle mode, apply to graph
        queries_toolbar_hbox = widgets.HBox([self.__delete_all_queries_button, self.__filter_highlight_toggle_buttons,
                                             self.__apply_to_graph_button])
        # Main toolbar : Operator Dropdown, Add Query Button
        main_toolbar_vbox = widgets.VBox(
            [widgets.HBox([self.__add_new_query_button, self.__add_new_neg_query_button, self.__queries_help_button]),
             self.__add_new_clause_msg_html])
        # form BOX
        queries_form_vbox = widgets.VBox(
            [self.__attributes_dropdown,
             widgets.HBox([self.__node_id_int_text, self.__nominal_value_dropdown,
                           self.__interval_value_float_slider, self.__ordinal_value_selection_range_slider,
                           self.__value_error_html]),
             widgets.HBox([self.__color_picker, self.__color_picker_msg_html]), main_toolbar_vbox])
        # Query output BOX
        self.__queries_output_box = widgets.Box([], layout=self.__filter_box_layout)

        # Put created components into correct container
        self.__queries_main_vbox.children = [queries_toolbar_hbox, queries_form_vbox, self.__queries_output_box,
                                             self.__relevant_nodes_accordion]

    def __build_on_attribute_change(self) -> typ.Callable:
        def on_change(change):
            if change['type'] == 'change' and change['name'] == 'value':
                selected_attribute = self.__attribute_info[self.__attributes_dropdown.value]
                self.__value_error_html.layout.display = 'none'
                if selected_attribute['measurement_type'] == 'N':  # Selected attribute is nominal
                    # Activate nominal value dropdown
                    self.__nominal_value_dropdown.options = selected_attribute['categories']
                    self.__nominal_value_dropdown.value = selected_attribute['categories'][0]
                    self.__nominal_value_dropdown.disabled = False
                    self.__nominal_value_dropdown.layout.display = 'inline-flex'
                    # Hide interval and ordinal value sliders and node id input
                    self.__interval_value_float_slider.layout.display = 'none'
                    self.__ordinal_value_selection_range_slider.layout.display = 'none'
                    self.__node_id_int_text.layout.display = 'none'
                elif selected_attribute['measurement_type'] == 'I':  # Selected attribute is interval
                    # Activate interval value slider
                    self.__interval_value_float_slider.disabled = False
                    self.__interval_value_float_slider.readout = True
                    # ipywidgets won't let us assign min > max, so we have to do this:
                    self.__interval_value_float_slider.max = sys.maxsize
                    self.__interval_value_float_slider.min = selected_attribute['range'][0]
                    self.__interval_value_float_slider.max = selected_attribute['range'][1]
                    self.__interval_value_float_slider.value = selected_attribute['range']
                    self.__interval_value_float_slider.layout.display = 'inline-flex'
                    # Hide nominal dropdown and ordinal slider and node id input
                    self.__nominal_value_dropdown.layout.display = 'none'
                    self.__ordinal_value_selection_range_slider.layout.display = 'none'
                    self.__node_id_int_text.layout.display = 'none'
                elif selected_attribute['measurement_type'] == 'O':  # Selected attribute is ordinal
                    # Activate ordinal value slider
                    self.__ordinal_value_selection_range_slider.disabled = False
                    self.__ordinal_value_selection_range_slider.readout = True
                    self.__ordinal_value_selection_range_slider.options = selected_attribute['categories']
                    self.__ordinal_value_selection_range_slider.index = (0, len(selected_attribute['categories']) - 1)
                    self.__ordinal_value_selection_range_slider.layout.display = 'inline-flex'
                    # Hide nominal dropdown and interval slider and node id input
                    self.__nominal_value_dropdown.layout.display = 'none'
                    self.__interval_value_float_slider.layout.display = 'none'
                    self.__node_id_int_text.layout.display = 'none'
                elif selected_attribute['measurement_type'] == 'ID':  # Selected attribute is Node ID
                    self.__node_id_int_text.disabled = False
                    self.__node_id_int_text.layout.display = 'inline-flex'
                    # Hide input widgets
                    self.__nominal_value_dropdown.layout.display = 'none'
                    self.__ordinal_value_selection_range_slider.layout.display = 'none'
                    self.__interval_value_float_slider.layout.display = 'none'

        return on_change

    def __construct_query(self, query_id: int):
        html_string = self.__construct_query_html(query_id)
        # lookup the widget and reassign the HTML value
        for i in range(len(self.__queries_output_box.children)):
            id_ = self.__queries_output_box.children[i].children[0].description
            if int(id_) == query_id:
                self.__queries_output_box.children[i].children[1].value = html_string
                break

    def __construct_queries_from_scratch(self) -> None:
        # Empty output box
        self.__queries_output_box.children = []
        # Add queries
        for query_id, query in self.__get_queries_reference().items():
            w_0 = widgets.Text(description=str(query_id), layout=widgets.Layout(display='none'))
            w_1 = widgets.HTML(value=self.__construct_query_html(query_id),
                               layout=widgets.Layout(display='inline-block'))
            self.__queries_output_box.children += (widgets.HBox([w_0, w_1], layout=widgets.Layout(display='block')),)

    def __construct_query_html(self, query_id: int) -> str:
        context = self.__create_query_context(query_id)
        html_string = pystache.render(self.__query_template, context)
        return html_string

    def __create_query_context(self, query_id) -> typ.Dict[str, typ.Any]:
        is_filter = self.in_filter_mode()
        is_active = query_id in self.__get_active_queries_reference()

        context = dict()
        context['query_id'] = str(query_id)
        context['toggle_state'] = ['fa-toggle-off', 'fa-toggle-on'][is_active]
        context['is_filter'] = is_filter
        context['color'] = self.__get_queries_reference()[query_id]['color']
        context['clauses'] = list()
        for key, clause in sorted(self.__get_queries_reference()[query_id]['clauses'].items(), key=lambda t: int(t[0])):
            clause_ctx = dict()
            clause_ctx['clause_id'] = str(key)
            clause_ctx['operator_new'] = clause['operator'] == 'NEW'
            clause_ctx['operator'] = clause['operator']
            clause_ctx['attribute_name'] = clause['value'][0]
            # Nominal and ID act similarly in how they are displayed.
            if self.__attribute_info[clause['value'][0]]['measurement_type'] in {'N', 'ID'}:
                clause_ctx['is_nominal'] = True
                clause_ctx['value'] = clause['value'][1]
            else:
                clause_ctx['is_nominal'] = False
                clause_ctx['value_begin'] = clause['value'][1][0]
                clause_ctx['value_end'] = clause['value'][1][1]
            context['clauses'].append(clause_ctx)
        return context

    def __create_queries_context(self) -> typ.Dict[str, typ.Any]:
        context = {'queries': list()}
        for query_id in sorted(self.__get_queries_reference().keys()):
            context['queries'].append(self.__create_query_context(query_id))
        return context

    def __construct_relevant_node_html(self, nodes_displayed: int) -> str:
        context = self.__create_queries_context()
        # Enrich context of each query with node count and string containing nodes_displayed node IDs.
        active_queries = dict((idx, query) for idx, query in self.__get_queries_reference().items()
                              if idx in self.__get_active_queries_reference())
        for i, query in enumerate(active_queries.values()):
            node_filter = build_clause(query['clauses'], self.__attribute_info)
            relevant_nodes = list(node_filter(self.__temp_graph.get_nodes()))
            context['queries'][i]['node_count'] = len(relevant_nodes)
            context['queries'][i]['first_nodes'] = ', '.join(str(node.get_id())
                                                             for node in relevant_nodes[:nodes_displayed])
            context['queries'][i]['are_all_nodes'] = len(relevant_nodes) <= nodes_displayed
        html_string = pystache.render(self.__relevant_node_template, context)
        return html_string

    def __update_relevant_node_id_summary(self):
        self.__relevant_nodes_overview_html.value = self.__construct_relevant_node_html(
            UIAttributeQueriesManager.RELEVANT_NODE_DISPLAY_LIMIT)

    def __fetch_current_value(self) -> typ.Any:
        attribute_type = self.__attribute_info[self.__attributes_dropdown.value]['measurement_type']
        return {'N': self.__nominal_value_dropdown.value,  # string
                'I': self.__interval_value_float_slider.value,  # tuple of 2 ints
                'O': self.__ordinal_value_selection_range_slider.value,  # tuple of 2 strings
                'ID': self.__node_id_int_text.value  # int
                }[attribute_type]

    def __build_add_query(self, negated: bool) -> typ.Callable:
        def on_click(_):
            active_queries = self.__get_active_queries_reference()
            query_counter_read = self.__get_query_counter()
            queries = self.__get_queries_reference()
            current_value = self.__fetch_current_value()

            self.__value_error_html.layout.display = 'none'
            # Check if input of Node ID is valid node
            if self.__attribute_info[self.__attributes_dropdown.value]['measurement_type'] == 'ID':
                if current_value not in self.__attribute_info[self.__attributes_dropdown.value]['ids']:
                    self.__value_error_html.value = '<span style="color: red;">No node with this ID exists</span>'
                    self.__value_error_html.layout.display = 'inline-flex'
                    return

            active_queries.append(query_counter_read)
            queries[query_counter_read] = \
                {'color': self.__color_picker.value,
                 'clauses': {1: {'operator': 'NOT' if negated else 'NEW',
                                 'value': (self.__attributes_dropdown.value, current_value)}}}
            w_0 = widgets.Text(description=str(query_counter_read), layout=widgets.Layout(display='none'))
            w_1 = widgets.HTML(value=' ', layout=widgets.Layout(display='inline-block'))
            self.__queries_output_box.children += (widgets.HBox([w_0, w_1], layout=widgets.Layout(display='block')),)
            self.__construct_query(query_counter_read)

            self.__increment_query_counter()

            self.__update_relevant_node_id_summary()
        return on_click

    def build_add_query_clause(self) -> typ.Callable:
        def on_click(query_id, operator):
            queries = self.__get_queries_reference()
            value = self.__fetch_current_value()
            new_clause_idx = int(max(queries[query_id]['clauses'].keys(), key=int)) + 1
            queries[query_id]['clauses'][new_clause_idx] = \
                {'operator': operator,
                 'value': (self.__attributes_dropdown.value, value)}
            self.__construct_query(query_id)
            self.__update_relevant_node_id_summary()
        return on_click

    def build_delete_query_clause(self) -> typ.Callable:
        def on_click(query_id, clause_id):
            queries = self.__get_queries_reference()
            clause = queries[query_id]['clauses'][clause_id]
            is_initial = clause['operator'] in {'NEW', 'NOT'}
            queries[query_id]['clauses'].pop(clause_id)
            if len(queries[query_id]['clauses']) == 0:
                queries.pop(query_id)
                self.__construct_queries_from_scratch()
            else:
                if is_initial:
                    # Find next clause in order after old initial one
                    new_initial_idx = min(queries[query_id]['clauses'].keys(), key=int)
                    queries[query_id]['clauses'][new_initial_idx]['operator'] = \
                        'NOT' if 'NOT' in queries[query_id]['clauses'][new_initial_idx]['operator'] else 'NEW'
                self.__construct_query(query_id)
            self.__update_relevant_node_id_summary()
        return on_click

    def build_delete_query(self) -> typ.Callable:
        def on_click(query_id):
            queries = self.__get_queries_reference()
            queries.pop(query_id)
            keep = []
            for i in range(len(self.__queries_output_box.children)):
                if str(query_id) != self.__queries_output_box.children[i].children[0].description:
                    keep.append(self.__queries_output_box.children[i])
            self.__queries_output_box.children = keep
            self.__update_relevant_node_id_summary()
        return on_click

    def build_switch_query(self) -> typ.Callable:
        def on_click(query_id):
            active_queries = self.__get_active_queries_reference()
            q_id = int(query_id)
            if q_id in active_queries:
                active_queries.remove(q_id)
            else:
                active_queries.append(q_id)
            self.__construct_query(q_id)
            self.__update_relevant_node_id_summary()
        return on_click

    def build_paint_query(self) -> typ.Callable:
        def on_click(query_id):
            queries = self.__get_queries_reference()
            queries[query_id]['color'] = self.__color_picker.value
            self.__construct_query(query_id)
            self.__update_relevant_node_id_summary()
        return on_click

    def __build_delete_all_queries(self) -> typ.Callable:
        def on_click(_):
            self.__queries_output_box.children = []
            self.__reset_queries()
            self.__reset_query_counter()
            self.__update_relevant_node_id_summary()
        return on_click

    def __build_on_mode_change(self) -> typ.Callable:
        def on_mode_change(change):
            if change['type'] == 'change' and change['name'] == 'value':
                ipydisplay.display(ipydisplay.Javascript("je.setMode('" + self.__filter_highlight_toggle_buttons.value
                                                         + "').switchMode();"))
                if self.__filter_highlight_toggle_buttons.value == 'Highlight':
                    self.__color_picker.layout.display = 'inline-flex'
                    self.__color_picker_msg_html.layout.display = 'inline-flex'
                elif self.__filter_highlight_toggle_buttons.value == 'Filter':
                    self.__color_picker.layout.display = 'none'
                    self.__color_picker_msg_html.layout.display = 'none'
                # Redraw queries output completely
                self.__construct_queries_from_scratch()
                self.__update_relevant_node_id_summary()
        return on_mode_change

    def in_filter_mode(self) -> bool:
        """Returns whether current mode is filter or not (highlight)"""
        return self.__filter_highlight_toggle_buttons.value == 'Filter'

    def __get_active_queries_reference(self) -> typ.List[int]:
        """Returns reference to active_queries list corresponding to the current mode: filter or highlight"""
        active_queries = self.__active_filter_queries if self.in_filter_mode() else self.__active_highlight_queries
        return active_queries

    def __get_queries_reference(self) -> typ.Dict:
        """Returns reference to queries dict corresponding to the current mode: filter or highlight"""
        return self.__filter_queries if self.in_filter_mode() else self.__highlight_queries

    def __reset_queries(self) -> None:
        """Empties queries of current mode: filter or highlight"""
        if self.in_filter_mode():
            self.__filter_queries = dict()
        else:
            self.__highlight_queries = dict()

    def __get_query_counter(self) -> int:
        """Returns the query count corresponding to the current mode: filter or highlight"""
        return self.__filter_query_counter if self.in_filter_mode() else self.__highlight_query_counter

    def __increment_query_counter(self) -> None:
        """Increments the query count corresponding to the current mode: filter or highlight"""
        if self.in_filter_mode():
            self.__filter_query_counter += 1
        else:
            self.__highlight_query_counter += 1

    def __reset_query_counter(self) -> None:
        """Resets the query count to 1 corresponding to the current mode: filter or highlight"""
        if self.in_filter_mode():
            self.__filter_query_counter = 1
        else:
            self.__highlight_query_counter = 1

    def get_node_filter(self) -> vtna.filter.NodeFilter:
        active_queries = dict((idx, query) for idx, query in self.__filter_queries.items()
                              if idx in self.__active_filter_queries)
        node_filter = transform_queries_to_filter(active_queries, self.__attribute_info)
        return node_filter

    def get_node_colors(self, temp_graph: vtna.graph.TemporalGraph, default_color: str) -> typ.Dict[int, str]:
        active_queries = dict((idx, query) for idx, query in self.__highlight_queries.items()
                              if idx in self.__active_highlight_queries)
        node_colors = transform_queries_to_color_mapping(active_queries, self.__attribute_info, temp_graph,
                                                         default_color)
        return node_colors

    # Observer pattern for updating observing UI managers
    # TODO: Generalize to arbitrary observers and observables?
    def register_graph_display_manager(self, manager: UIGraphDisplayManager):
        self.__graph_display_managers.append(manager)

    def __notify_all(self):
        for manager in self.__graph_display_managers:
            manager.notify(self)

    def get_apply_button(self):
        return self.__apply_to_graph_button


def transform_queries_to_filter(queries: typ.Dict, attribute_info: typ.Dict) -> vtna.filter.NodeFilter:
    clauses = list()  # type: typ.List[vtna.filter.NodeFilter]
    for raw_clause in map(lambda t: t[1]['clauses'], sorted(queries.items(), key=lambda t: int(t[0]))):
        clause = build_clause(raw_clause, attribute_info)
        clauses.append(clause)
    result_filter = None
    for i, clause in enumerate(clauses):
        if i == 0:
            result_filter = clause
        else:
            result_filter += clause
    if result_filter is None:
        result_filter = vtna.filter.NodeFilter(lambda _: True)
    return result_filter


def transform_queries_to_color_mapping(queries: typ.Dict, attribute_info: typ.Dict,
                                       temp_graph: vtna.graph.TemporalGraph, default_color: str) \
        -> typ.Dict[int, str]:
    # Init all nodes with the default color
    colors = dict((node.get_id(), default_color) for node in temp_graph.get_nodes())
    for raw_clauses in map(lambda t: t[1], sorted(queries.items(), key=lambda t: int(t[0]), reverse=True)):
        raw_clause = raw_clauses['clauses']
        color = raw_clauses['color']
        clause = build_clause(raw_clause, attribute_info)
        nodes_to_color = clause(temp_graph.get_nodes())
        for node_id in map(lambda n: n.get_id(), nodes_to_color):
            colors[node_id] = color
    return colors


def build_clause(raw_clause: typ.Dict, attribute_info: typ.Dict) \
        -> vtna.filter.NodeFilter:
    clause = None
    for raw_predicate in map(lambda t: t[1], sorted(raw_clause.items(), key=lambda t: int(t[0]))):
        predicate = build_predicate(raw_predicate, attribute_info)
        node_filter = vtna.filter.NodeFilter(predicate)
        # Case distinction for different operators:
        op = raw_predicate['operator']
        if op == 'NEW':
            clause = node_filter
        elif op == 'NOT':
            clause = -node_filter
        elif op == 'AND':
            clause *= node_filter
        elif op == 'OR':
            clause += node_filter
        elif op == 'AND NOT':
            clause *= -node_filter
        elif op == 'OR NOT':
            clause += -node_filter
        else:
            # Either the front end provided a bad queries dict, or the matching is not correct.
            raise Exception(f'Abnormal Behaviour: Unknown queries combinator: {op}')
    return clause


def build_predicate(raw_predicate: typ.Dict, attribute_info: typ.Dict) \
        -> typ.Callable[[vtna.graph.TemporalNode], bool]:
    # TODO: Currently assumes only string type values. More case distinctions needed for more complex types.
    # TODO: Can ordinal or nominal attributes be local as well?
    # build_predicate assumes correctness of the input in regards to measure type assumptions.
    # e.g. range type queries will only be made for truly ordinal or interval values.
    name, value = raw_predicate['value']
    if attribute_info[name]['measurement_type'] == 'O':
        order = attribute_info[name]['categories']
        lower_bound = vtna.filter.ordinal_attribute_greater_than_equal(name, value[0], order)
        inv_upper_bound = vtna.filter.ordinal_attribute_greater_than(name, value[1], order)
        pred = lambda n: lower_bound(n) and not inv_upper_bound(n)
    elif attribute_info[name]['measurement_type'] == 'I':
        lower_bound = vtna.filter.interval_attribute_greater_than_equal(name, value[0])
        inv_upper_bound = vtna.filter.interval_attribute_greater_than(name, value[1])
        pred = lambda n: lower_bound(n) and not inv_upper_bound(n)
    elif attribute_info[name]['measurement_type'] == 'N':  # Equality
        pred = vtna.filter.categorical_attribute_equal(name, value)
    elif attribute_info[name]['measurement_type'] == 'ID':
        pred = lambda n: n.get_id() == value
    else:
        raise Exception(f'Abnormal Behaviour: Unexpected measurement type: {attribute_info[name]["measurement_type"]}')
    return pred


class NodeMeasuresManager(object):
    # A dictionary is used for easier returning of specific measures
    node_measure_classes = {
        measure.get_name(): measure for measure in
        [
            vtna.node_measure.LocalDegreeCentrality,
            vtna.node_measure.GlobalDegreeCentrality,
            vtna.node_measure.LocalBetweennessCentrality,
            vtna.node_measure.GlobalBetweennessCentrality,
            vtna.node_measure.LocalClosenessCentrality,
            vtna.node_measure.GlobalClosenessCentrality
        ]
    }

    def __init__(self, temporal_graph: vtna.graph.TemporalGraph, requested_node_measures: typ.List[str]):
        """
        Computes specified node measures without attaching them to the graph.

        Args:
            requested_node_measures: List of keyword strings of dictionary
                UINodeMeasuresManager.node_measures
        Raises:
            DuplicateMeasuresError: If a measure is specified multiple times
        """
        self.__node_measures: typ.Dict[str, vtna.node_measure.NodeMeasure]

        # Prevent duplicate measures
        measure_type_counter = collections.Counter()
        measure_type_counter.update(requested_node_measures)
        duplicate_names = set(n for n, c in measure_type_counter.items() if c > 1)
        if len(duplicate_names) > 0:
            raise self.DuplicateMeasuresError(duplicate_names)

        # Instantiate and compute node measures
        self.__node_measures = dict(
            [(nm, self.node_measure_classes[nm](temporal_graph)) for nm in requested_node_measures])

    def add_all_to_graph(self):
        """Adds all currently computed node measures to the temporal graph."""
        for nm in self.__node_measures.values():
            nm.add_to_graph()

    def get_node_measure(self, node_measure_type: str):
        """Returns NodeMeasure object of provided type."""
        return self.__node_measures[node_measure_type]

    class DuplicateMeasuresError(ValueError):
        def __init__(self, names: typ.Set[str]):
            self.message = f'Node measures {", ".join(names)} are duplicates'
            self.illegal_names = names


class TemporalGraphFigure(object):
    DEFAULT_ANIMATION_FRAME_LENGTH = 700

    def __init__(self,
                 temp_graph: vtna.graph.TemporalGraph,
                 layout: typ.List[typ.Dict[int, typ.Tuple[float, float]]],
                 display_size: typ.Tuple[int, int],
                 animate_transitions: bool,
                 color_map: typ.Union[str, typ.Dict[int, str]],
                 edge_color: str,
                 node_size: float,
                 edge_width: float):
        self.__temp_graph = temp_graph
        # Retrieve nodes once to ensure same order
        self.__nodes = self.__temp_graph.get_nodes()
        self.__layout = layout
        self.__display_size = display_size
        self.__color_map = color_map
        self.__edge_color = edge_color
        self.__node_size = node_size
        self.__edge_width = edge_width

        self.__node_filter = vtna.filter.NodeFilter(lambda _: True)
        self.__figure_data = None  # type: typ.Dict
        self.__sliders_data = None  # type: typ.Dict
        self.__figure_plot = None  # type: plt.Figure
        self.__transition_time = 300
        self.__frame_length = TemporalGraphFigure.DEFAULT_ANIMATION_FRAME_LENGTH
        self.toggle_animate_transitions(animate_transitions)
        self.__build_data_frames()

    def __init_figure_data(self):
        self.__figure_data = {
            'data': [],
            'layout': {},
            'frames': []
        }
        self.__figure_data['layout']['showlegend'] = False
        self.__figure_data['layout']['autosize'] = False
        # Substract approximate height of control widgets to fit in the box
        self.__figure_data['layout']['width'] = self.__display_size[0] - 20
        self.__figure_data['layout']['height'] = self.__display_size[1] - 20
        # Make plot more compact
        self.__figure_data['layout']['margin'] = plotly.graph_objs.Margin(
            t=20,
            pad=0
        )
        self.__figure_data['layout']['hovermode'] = 'closest'
        self.__figure_data['layout']['yaxis'] = {
            'range': [-1.1, 1.1],
            'ticks': '',
            'showticklabels': False
        }
        self.__figure_data['layout']['xaxis'] = {
            'range': [-1.1, 1.1],
            'ticks': '',
            'showticklabels': False
        }
        self.__figure_data['layout']['sliders'] = {
            'args': [
                'transition', {
                    'duration': self.__transition_time,
                    'easing': 'cubic-in-out',
                }
            ],
            'initialValue': '0',
            'plotlycommand': 'animate',
            'values': len(self.__temp_graph),
            'visible': True
        }
        self.__figure_data['layout']['updatemenus'] = [
            {
                'buttons': [
                    {
                        'args': [None, {'frame': {'duration': self.__frame_length, 'redraw': False},
                                        'fromcurrent': True,
                                        'transition': {'duration': self.__transition_time, 'easing': 'quadratic-in-out'}}],
                        'label': 'Play',
                        'method': 'animate'
                    },
                    {
                        'args': [[None], {'frame': {'duration': 0, 'redraw': False}, 'mode': 'immediate',
                                          'transition': {'duration': 0}}],
                        'label': 'Pause',
                        'method': 'animate'
                    }
                ],
                'direction': 'left',
                'pad': {'r': 10, 't': 37},
                'showactive': False,
                'type': 'buttons',
                'x': 0.1,
                'xanchor': 'right',
                'y': 0,
                'yanchor': 'top'
            }
        ]
        self.__sliders_data = {
            'active': 0,
            'yanchor': 'top',
            'xanchor': 'left',
            'currentvalue': {
                'font': {'size': 20},
                'prefix': '+',
                'suffix': ' hours',
                'visible': True,
                'xanchor': 'right'
            },
            'transition': {'duration': self.__transition_time, 'easing': 'immediate'},
            'pad': {'b': 10, 't': 0},
            'len': 0.9,
            'x': 0.1,
            'y': 0,
            'steps': []
        }

    def get_figure(self) -> typ.Dict:
        return self.__figure_data

    def toggle_animate_transitions(self, animate_transitions: bool):
        """Toggles transition animation. Must be called before frames are built."""
        if animate_transitions:
            self.__transition_time = 300
        else:
            self.__transition_time = 0

    def update_colors(self, color_map: typ.Union[str, typ.Dict[int, str]]):
        if self.__color_map != color_map:
            self.__color_map = color_map
            self.__recolor_displayed_nodes()
            self.__set_figure_data_as_initial_frame()

    def update_filter(self, node_filter: vtna.filter.NodeFilter):
        self.__node_filter = node_filter
        self.__build_data_frames()

    def update_layout(self, layout: typ.List[typ.Dict[int, typ.Tuple[float, float]]]):
        self.__layout = layout
        self.__build_data_frames()

    def update_edge_color(self, color: str):
        if self.__edge_color != color:
            self.__edge_color = color
            self.__recolor_displayed_edges()
            self.__set_figure_data_as_initial_frame()

    def update_node_size(self, size: float):
        if self.__node_size != size:
            self.__node_size = size
            self.__resize_displayed_nodes()
            self.__set_figure_data_as_initial_frame()

    def update_edge_width(self, width: float):
        if self.__edge_width != width:
            self.__edge_width = width
            self.__resize_displayed_edges()
            self.__set_figure_data_as_initial_frame()

    def update_animation_frame_length(self, frame_length: int):
        # Don't do anything on unchanged value
        if frame_length != self.__frame_length:
            self.__frame_length = frame_length
            # Update play animation speed with a beautiful 9-layer-deep container access
            self.__figure_data['layout']['updatemenus'][0]['buttons'][0]['args'][1]['frame']['duration'] = frame_length

    def __build_data_frames(self):
        self.__init_figure_data()

        node_ids = [node.get_id() for node in self.__node_filter(self.__temp_graph.get_nodes())]

        for timestep, graph in enumerate(self.__temp_graph):
            edge_trace = plotly.graph_objs.Scatter(
                x=[],
                y=[],
                ids=[],
                mode='lines',
                line={
                    'width': self.__edge_width,
                    'color': self.__edge_color
                }
            )
            node_trace = plotly.graph_objs.Scatter(
                x=[],
                y=[],
                ids=[],
                text=[],
                mode='markers',
                hoverinfo='text',
                marker={
                    'size': self.__node_size,
                    'color': self.__color_map
                }
            )

            used_node_ids = set()
            # Add edges to data
            for edge in graph.get_edges():
                node1, node2 = edge.get_incident_nodes()
                # Only display edges of visible nodes
                if node1 in node_ids and node2 in node_ids:
                    x1, y1 = self.__layout[timestep][node1]
                    x2, y2 = self.__layout[timestep][node2]
                    edge_trace['x'].extend([x1, x2, None])
                    edge_trace['y'].extend([y1, y2, None])
                    edge_trace['ids'].extend([node1, node2, 0])
                    # Only nodes with VISIBLE edges are displayed.
                    used_node_ids.add(node1)
                    used_node_ids.add(node2)

            if isinstance(self.__color_map, dict):
                colors = [self.__color_map[node_id] for node_id in used_node_ids]
            else:
                colors = self.__color_map
            node_trace['marker']['color'] = colors

            # Add nodes to data
            for node_id in used_node_ids:
                x, y = self.__layout[timestep][node_id]
                node_trace['x'].append(x)
                node_trace['y'].append(y)
                node_trace['ids'].append(node_id)

                # Add attribute info for hovering
                info_text = f'<b style="color:#4caf50">ID:</b> {node_id}<br>'
                # Add global attributes info
                global_attribute_names = [n for (n, info) in self.__temp_graph.get_attributes_info().items() if
                                          info['scope'] == 'global']
                if len(global_attribute_names) > 0:
                    info_text += '<b style="color:#91dfff">Global:</b><br>'
                for attribute_name in global_attribute_names:
                    attribute_value = self.__temp_graph.get_node(node_id).get_global_attribute(attribute_name)
                    info_text += f"{attribute_name}: {attribute_value}<br>"
                # Add local attributes info
                local_attribute_names = [n for (n, info) in self.__temp_graph.get_attributes_info().items() if
                                         info['scope'] == 'local']
                if len(local_attribute_names) > 0:
                    info_text += '<b style="color:#91dfff">Local:</b><br>'
                for attribute_name in local_attribute_names:
                    attribute_value = self.__temp_graph.get_node(node_id).get_local_attribute(attribute_name, timestep)
                    info_text += f"{attribute_name}: {attribute_value}<br>"
                node_trace['text'].append(info_text)

            frame = {'data': [edge_trace, node_trace], 'name': str(timestep)}
            self.__figure_data['frames'].append(frame)

            slider_step = {
                'args': [
                    [timestep],
                    {
                        'frame': {'duration': 0, 'redraw': False},
                        'mode': 'immediate',
                        'transition': {'duration': self.__transition_time}
                    }
                ],
                'label': str(datetime.timedelta(seconds=timestep * self.__temp_graph.get_granularity())),
                'method': 'animate'
            }
            self.__sliders_data['steps'].append(slider_step)
        self.__figure_data['layout']['sliders'] = [self.__sliders_data]

        self.__set_figure_data_as_initial_frame()

    def __recolor_displayed_nodes(self):
        node_ids = [node.get_id() for node in self.__node_filter(self.__nodes)]
        for i in range(len(self.__figure_data['frames'])):
            used_node_ids = set(node for edge in self.__temp_graph[i].get_edges() for node in edge.get_incident_nodes())
            used_node_ids = list(filter(lambda n: n in used_node_ids, node_ids))
            if isinstance(self.__color_map, dict):
                colors = [self.__color_map[node_id] for node_id in used_node_ids]
            else:
                colors = self.__color_map
            self.__figure_data['frames'][i]['data'][1]['marker']['color'] = colors

    def __recolor_displayed_edges(self):
        for i in range(len(self.__figure_data['frames'])):
            self.__figure_data['frames'][i]['data'][0]['line']['color'] = self.__edge_color

    def __resize_displayed_nodes(self):
        for i in range(len(self.__figure_data['frames'])):
            self.__figure_data['frames'][i]['data'][1]['marker']['size'] = self.__node_size

    def __resize_displayed_edges(self):
        for i in range(len(self.__figure_data['frames'])):
            self.__figure_data['frames'][i]['data'][0]['line']['width'] = self.__edge_width

    def __set_figure_data_as_initial_frame(self):
        # Call this method after completing changes in __figure_data
        self.__figure_data['data'] = self.__figure_data['frames'][0]['data'].copy()


class VideoExport(object):
    ffmpeg_formats = ['mp4', 'mov', 'avi']

    def __init__(self,
                 figure: typ.Dict,
                 video_format: str,
                 video_resolution: int,
                 frame_length: int,
                 time_range: typ.Tuple[int, int],
                 speedup_empty_frames: bool,
                 initialize_progressbar: typ.Callable,
                 increment_progress: typ.Callable,
                 progress_finished: typ.Callable):
        # We need the amount of frames and the counter for syncing the asynchron js writing
        # with the closing of the writer and the progress bar
        self.__frames = figure['frames']
        self.__frame_count = time_range[1] - time_range[0]
        # Milliseconds are converted to seconds
        frame_length /= 1000
        # There are two steps for every frame: Extracting via js and writing to gif
        initialize_progressbar(self.__frame_count * 2)
        self.__increment_progress = increment_progress  # type: typ.Callable
        self.__progress_finished = progress_finished  # type: typ.Callable
        self.__export_filename = time.strftime('%Y%m%d-%H%M', time.localtime()) + '_export'
        self.__video_format = video_format
        if video_format == 'gif':
            # Length of a GIF frame
            duration = frame_length
            # Compute speed up duration list
            if speedup_empty_frames:
                # GIF cant have more than 100 FPS
                speedup_length = frame_length / 10 if frame_length / 10 >= 0.01 else 0.01
                duration = [frame_length if len(frame['data'][1]['x']) > 0 else speedup_length
                            for frame in self.__frames[time_range[0]:time_range[1] + 1]]
            # Create the writer object for creating the gif.
            # Mode I tells the writer to prepare for multiple images.
            self.__writer = imageio.get_writer(self.__export_filename + '.gif', mode='I', duration=duration)
        elif video_format in VideoExport.ffmpeg_formats:
            self.__writer = imageio.get_writer(self.__export_filename + '.' + video_format, format='ffmpeg', mode='I',
                                               fps=1/frame_length)
        else:
            raise ValueError('Unknown format: ' + video_format)

        self.__init_figure(figure['layout']['sliders'][0]['steps'], video_resolution)

        self.__build_index = 0
        self.__written_frames = 0
        self.__output = widgets.Output(layout=widgets.Layout(display='none'))
        ipydisplay.display(self.__output)
        # Start building the frames
        self.__build_frame()

    def get_output_path(self):
        return self.__export_filename + '.' + self.__video_format

    def __init_figure(self, steps, size: int):
        self.__figure = {'layout': {}}
        # First we build the layout of the plot that will be exported
        # TODO: Layout should be at least partially dependent/copied from original plotly layout
        self.__figure['layout']['width'] = size
        self.__figure['layout']['height'] = size
        self.__figure['layout']['showlegend'] = False
        # Make plot more compact
        self.__figure['layout']['margin'] = plotly.graph_objs.Margin(
            t=30,
            r=30,
            b=30,
            l=30,
            pad=0
        )
        self.__figure['layout']['yaxis'] = {
            'range': [-1.1, 1.1],
            'ticks': '',
            'showticklabels': False
        }
        self.__figure['layout']['xaxis'] = {
            'range': [-1.1, 1.1],
            'ticks': '',
            'showticklabels': False
        }
        self.__figure['layout']['sliders'] = [{
            'currentvalue': {
                'font': {'size': 20},
                'prefix': 'Timestep: +',
                'suffix': ' hours',
                'visible': True,
                'xanchor': 'right'
            },
            'pad': {'b': 10, 't': 20},
            'steps': steps
        }]

    def __build_frame(self):
        # Add current plot data (of this frame)
        self.__figure['data'] = self.__frames[self.__build_index]['data']
        # Position dummy slider on current timestep
        self.__figure['layout']['sliders'][0]['active'] = self.__build_index
        with self.__output:
            # noinspection PyTypeChecker
            ipydisplay.display(ipydisplay.HTML(
                # plot() returns the html div with the plot itself.
                # Not including plotlyjs improves performance, and its already
                # loaded in the notebook anyways.
                plotly.offline.plot(self.__figure, output_type='div', include_plotlyjs=False)
                # Execute the javascript that extracts the image.
                # See export.js for function implementations.
                + f'''
                <script>
                    extractPlotlyImage(); 
                </script>'''
            ))
            # Remove plot again to save memory
            ipydisplay.clear_output()
        self.__build_index += 1
        self.__increment_progress()

    # This has to be public, so the GraphDisplayManager/the Notebook/above JS code
    # can access this non-static method.
    def write_frame(self, img_base64):
        try:
            # Decode base64 string to binary
            img_binary = base64.decodebytes(img_base64)
            # Append binary png image to gif writer
            self.__writer.append_data(imageio.imread(img_binary))
            self.__written_frames += 1
            self.__increment_progress()
            if self.__written_frames == self.__frame_count:
                self.__finish()
            else:
                # The next frame is built after this method/the js code is done
                # This prevents memory leaks caused by asynchronous execution
                self.__build_frame()
        except Exception as e:
            self.__writer.close()
            # TODO: Show as user-friendly error message
            print(e)

    def __finish(self):
        # Flushes and closes the writer
        self.__writer.close()
        self.__progress_finished()


class LoadingIndicator(object):
    loading_images = {
        'big': "images/loading.svg",
        'small': "images/loading_small.svg"
    }

    def __init__(self, size: str, outer_layout: widgets.Layout):
        """
        An SVG loading indicator that can be stopped/hidden and resumed.
        Provides a widget.Box that uses size of specified layout to act as placeholder,
        and contains the actual loading indicator.
        Initially the box is hidden and must be made visible with start().

        Args:
            size: String that indicates size of loading icon graphic. Can be 'small' or 'big'.
            outer_layout: A layout which size parameters will be used for the box layout as placeholder.
        """
        if size not in LoadingIndicator.loading_images:
            raise ValueError(f"'{size}' is not a valid size. Must be one of {LoadingIndicator.loading_images.keys()}")
        self.__size = size
        self.__output = widgets.Output()
        # Copy size parameters and center the content/loading indicator itself
        layout = widgets.Layout(
            width=outer_layout.width,
            height=outer_layout.height,
            align_items='center',
            justify_content='center'
        )
        self.__box = widgets.VBox(children=[self.__output], layout=layout)
        self.stop()

    def get_box(self):
        return self.__box

    def start(self):
        """Shows the loading indicator."""
        with self.__output:
            ipydisplay.clear_output()  # With this line duplicate calls of start will not destroy anything
            ipydisplay.display(ipydisplay.SVG(filename=LoadingIndicator.loading_images[self.__size]))
        self.__box.layout.display = 'flex'

    def stop(self):
        """Hides the loading indicator."""
        with self.__output:
            ipydisplay.clear_output()
        self.__box.layout.display = 'none'


class UIDefaultStyleOptionsManager(object):
    INIT_NODE_COLOR = '#000000'
    INIT_EDGE_COLOR = '#000000'
    INIT_NODE_SIZE = 10.0
    INIT_EDGE_SIZE = 0.6

    def __init__(self, options_vbox: widgets.VBox):
        self.__options_vbox = options_vbox
        self.__graph_display_managers = list()  # type: typ.List[UIGraphDisplayManager]

        self.__node_color_picker = widgets.ColorPicker(
            concise=False,
            value=UIDefaultStyleOptionsManager.INIT_NODE_COLOR,
            disabled=False,
            layout=widgets.Layout(width='8em')
        )

        self.__edge_color_picker = widgets.ColorPicker(
            concise=False,
            value=UIDefaultStyleOptionsManager.INIT_EDGE_COLOR,
            disabled=False,
            layout=widgets.Layout(width='8em')
        )

        self.__node_size_float_text = widgets.FloatText(
            value=UIDefaultStyleOptionsManager.INIT_NODE_SIZE,
            min=0.0,
            layout=widgets.Layout(width='8em')
        )

        self.__edge_size_float_text = widgets.FloatText(
            value=UIDefaultStyleOptionsManager.INIT_EDGE_SIZE,
            min=0.0,
            layout=widgets.Layout(width='8em')
        )

        self.__animation_speed_text = widgets.IntText(
            value=TemporalGraphFigure.DEFAULT_ANIMATION_FRAME_LENGTH,
            layout=widgets.Layout(width='12em'),
            description='Frame length:'
        )

        self.__apply_changes_button = widgets.Button(
            description='Apply',
            disabled=False,
            button_style='primary',
            tooltip='Apply default style',
            layout=widgets.Layout(top='0.5em')
        )
        self.__apply_changes_button.on_click(self.__build_on_change())

        self.__options_vbox.children = [
            widgets.HBox([
                widgets.VBox([widgets.Label('Node'), self.__node_color_picker, self.__node_size_float_text]),
                widgets.VBox([widgets.Label('Edge'), self.__edge_color_picker, self.__edge_size_float_text])
            ]),
            widgets.HBox([
                self.__animation_speed_text,
                widgets.Label(value='ms')
            ], layout=widgets.Layout(top='0.2em')),
            self.__apply_changes_button
        ]

    def register_graph_display_manager(self, manager: UIGraphDisplayManager):
        self.__graph_display_managers.append(manager)

    def __build_on_change(self) -> typ.Callable:
        def on_change(_):
            self.__apply_changes_button.disabled = True
            tmp_description = self.__apply_changes_button.description
            self.__apply_changes_button.description = 'Loading...'
            self.__absolute_all_size_inputs()
            self.__notify_all_graph_display_managers()
            self.__apply_changes_button.description = tmp_description
            self.__apply_changes_button.disabled = False

        return on_change

    def __notify_all_graph_display_managers(self):
        for manager in self.__graph_display_managers:
            manager.notify(self)

    def __absolute_all_size_inputs(self):
        self.__edge_size_float_text.value = abs(self.__edge_size_float_text.value)
        self.__node_size_float_text.value = abs(self.__node_size_float_text.value)

    def get_apply_button(self):
        return self.__apply_changes_button

    def get_node_color(self) -> str:
        return self.__node_color_picker.value

    def get_edge_color(self) -> str:
        return self.__edge_color_picker.value

    def get_node_size(self) -> float:
        return self.__node_size_float_text.value

    def get_edge_width(self) -> float:
        return self.__edge_size_float_text.value

    def get_animation_frame_length(self) -> int:
        return self.__animation_speed_text.value


class UIStatisticsManager(object):
    def __init__(self,
                 graph_header_hbox: widgets.HBox,
                 graph_summary_hbox: widgets.HBox,
                 node_summary_hbox: widgets.HBox,
                 node_search_vbox: widgets.VBox,
                 node_detailed_view_vbox: widgets.VBox,
                 graph_plots_hbox: widgets.HBox,
                 graph_summary_template_path: str,
                 graph_header_template_path: str
                 ):
        self.__graph_summary_html = widgets.HTML(layout=widgets.Layout(width='100%'))
        self.__graph_header_html = widgets.HTML(layout=widgets.Layout(width='100%'))
        graph_summary_hbox.children = [self.__graph_summary_html]
        graph_header_hbox.children = [self.__graph_header_html]

        self.__node_summary_html = widgets.HTML()
        node_summary_hbox.children = [self.__node_summary_html]

        with open(graph_summary_template_path) as f:
            self.__graph_summary_template = f.read()
        with open(graph_header_template_path) as f:
            self.__graph_header_template = f.read()

        self.__global_degree_distribution_plot = widgets.Output()
        self.__edge_bar_plot = widgets.Output()
        self.__attributes_dropdown = widgets.Dropdown(description='Attribute:')
        self.__attributes_dropdown.layout.display = 'none'
        self.__attribute_plot = widgets.Output()
        graph_plots_hbox.children = [widgets.VBox([self.__edge_bar_plot]),
                                     widgets.VBox([self.__attribute_plot, self.__attributes_dropdown]),
                                     help_widget(HELP_TEXT['statistics'])]
        self.__attribute_info = None
        self.__temp_graph = None  # type: vtna.graph.TemporalGraph

    def load(self, temp_graph: vtna.graph.TemporalGraph):
        self.__temp_graph = temp_graph
        self.__attribute_info = self.__temp_graph.get_attributes_info()
        self.__display_graph_header()
        self.__display_graph_summary()
        self.__display_interaction_distribution_plot()
        self.__build_attribute_dropdown()

    def __display_graph_header(self):
        if self.__temp_graph is None:
            return
        total_nodes = len(self.__temp_graph.get_nodes())
        total_edges = len(set(edge.get_incident_nodes()
                              for graph in self.__temp_graph.__iter__() for edge in graph.get_edges()))
        html = pystache.render(self.__graph_header_template,
                               {
                                   'total_nodes': total_nodes,
                                   'total_edges': total_edges,
                               })
        self.__graph_header_html.value = html

    def __display_graph_summary(self):
        if self.__temp_graph is None:
            return
        total_nodes = len(self.__temp_graph.get_nodes())
        total_edges = len(set(edge.get_incident_nodes()
                              for graph in self.__temp_graph.__iter__() for edge in graph.get_edges()))
        html = pystache.render(self.__graph_summary_template,
                               {
                                   'total_nodes': total_nodes,
                                   'total_edges': total_edges,
                               })
        self.__graph_summary_html.value = html

    def __display_interaction_distribution_plot(self):
        if self.__temp_graph is None:
            return
        timestamps = [timestamp for graph in self.__temp_graph for edge in graph.get_edges() for timestamp in edge.get_timestamps()]
        # Normalize timestamps, scale to minutes
        earliest = min(timestamps)
        timestamps = [(timestamp-earliest)/3600.0 for timestamp in timestamps]
        fig = plt.figure()
        ax = fig.gca()
        _ = sns.distplot(timestamps, hist=True, kde=True, bins=len(self.__temp_graph))
        plt.xlabel('Time in hours')
        plt.ylabel('Interactions')
        plt.title('Distribution of interactions over time')
        # Replace density values on yaxis with counts
        total = len(timestamps)
        locs, _ = plt.yticks()
        ax.set_yticklabels([int(total*density) for density in locs])
        with self.__edge_bar_plot:
            ipydisplay.clear_output()
            plt.show()

    def __build_attribute_dropdown(self):
        if self.__temp_graph is None:
            return

        attributes = list(filter(lambda a: self.__attribute_info[a]['scope'] == 'global', self.__attribute_info.keys()))

        if len(attributes) >= 1:
            # Attribute drop down
            self.__attributes_dropdown.options = attributes
            self.__attributes_dropdown.layout.width = f'{14+max(len(attribute) for attribute in attributes)}rem'
            self.__attributes_dropdown.observe(self.__build_on_attribute_change())
            self.__attributes_dropdown.layout.display = 'flex'
            self.__build_statistics_plot(attributes[0])

    def __build_on_attribute_change(self):
        def on_change(change):
            if change['type'] == 'change' and change['name'] == 'value':
                self.__build_statistics_plot(self.__attributes_dropdown.value)

        return on_change

    def __build_statistics_plot(self, attribute_value):
        selected_attribute = self.__attribute_info[attribute_value]
        attribute_values = [n.get_global_attribute(attribute_value) for n in
                            self.__temp_graph.get_nodes()]
        _ = plt.figure()
        if selected_attribute['measurement_type'] == 'I':
            _ = plt.hist(attribute_values, 75, alpha=0.75)
        else:
            categories = selected_attribute['categories']
            counts = [attribute_values.count(c) for c in categories]
            _ = plt.barh(categories, counts, align='center')
        plt.xlabel(attribute_value)
        plt.ylabel('Counts')
        plt.title(attribute_value + " distribution")
        with self.__attribute_plot:
            ipydisplay.clear_output()
            plt.show()
