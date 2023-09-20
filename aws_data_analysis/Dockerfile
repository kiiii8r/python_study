FROM continuumio/miniconda3

RUN set -x
RUN apt update -y
RUN apt upgrade -y

RUN apt install -y wget \
    sudo \
    vim

RUN conda update -y conda
RUN conda install -y pandas \
    matplotlib \
    scikit-learn \
    jupyter \
    lightgbm \
    boto3