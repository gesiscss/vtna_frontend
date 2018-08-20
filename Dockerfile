FROM python:3.6
LABEL version="0.1" description="Installs the application of the VTNA project at the University Koblenz" maintainer="marvinforster@uni-koblenz.de"

USER root

# Update system and add required packages
RUN apt-get -y update && apt-get -y upgrade
RUN echo deb http://ftp.uk.debian.org/debian jessie-backports main >> /etc/apt/sources.list
RUN apt-get -y update
RUN apt-get -y install ffmpeg

# Create user named vtna
RUN adduser --system --uid 1000 vtna 

# Create folder for storing the data of the application
RUN mkdir -p /usr/src/vtna/
WORKDIR /usr/src/vtna/

# Copy the data for the frontend and the jupyter notebook
COPY frontend ./frontend
RUN chown -R vtna:1000 /usr/src/vtna/

# Change to the created user vtna
USER vtna

# Install the required python packages in the home of the user
RUN pip install --no-cache-dir -r frontend/requirements.txt --user
RUN pip install --no-cache-dir git+https://github.com/marvinf95/vtna.git --user
RUN pip install --no-cache-dir appmode --user

# Delete unnacessary files
RUN rm frontend/requirements.txt

# Add the binaries from the home of the user vtna to the path
ENV PATH="/home/vtna/.local/bin:${PATH}"

# Extensions that are needed to show widgets in the notebook 
RUN jupyter nbextension enable --py widgetsnbextension --user && jupyter nbextension install --py fileupload --user && jupyter nbextension enable --py fileupload --user
RUN jupyter nbextension enable --py --user appmode
RUN jupyter serverextension enable --py --user appmode

# The notebook could be reached on port 8888
EXPOSE 8888

# Start the notebook in the container
CMD jupyter notebook --ip 0.0.0.0 --no-browser frontend/vtna.ipynb
