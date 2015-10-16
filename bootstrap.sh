#!/bin/sh -e

sudo yum-config-manager --enable epel
sudo yum -y install geos proj proj-nad proj-epsg
sudo ln -s /usr/lib64/libproj.so.0 /usr/lib64/libproj.so

curl http://oam-server-tiler.s3.amazonaws.com/emr/gdal-1.11.2-amz1.tar.gz | sudo tar zxf - -C /usr/local

cat << EOF > /tmp/requirements.txt
boto3==1.1.4
docopt==0.6.2
mercantile==0.8.3
psutil==3.2.2
rasterio==0.28.0
EOF

sudo GDAL_CONFIG=/usr/local/bin/gdal-config pip-2.7 install -r /tmp/requirements.txt
