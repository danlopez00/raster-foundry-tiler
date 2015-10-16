#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import boto3


def create_cluster():
    """
    Create new EMR cluster.
    """
    instances = {
        # For debugging only.
        'Ec2KeyName': 'rf-emr',
        'KeepJobFlowAliveWhenNoSteps': True,

        'InstanceGroups': [
            {
                'Name': 'Master',
                'InstanceRole': 'MASTER',
                'InstanceType': 'm3.xlarge',
                'InstanceCount': 1,
            },
            {
                'Name': 'Workers',
                'InstanceRole': 'CORE',
                'InstanceType': 'm3.xlarge',

                # For debugging only.
                # 'Market': 'SPOT',
                # 'BidPrice': '0.15',

                'Market': 'ON_DEMAND',
                'InstanceCount': 10,
            },
        ],
    }

    config_env_vars = {
        'Classification': 'export',
        'Properties': {
            'GDAL_DATA': '/usr/local/share/gdal',
            'LD_LIBRARY_PATH': '/usr/local/lib',
            'PYSPARK_PYTHON': 'python27',
            'PYSPARK_DRIVER_PYTHON': 'python27',
        },
    }

    configurations = [
        {
            'Classification': 'hadoop-env',
            'Configurations': [config_env_vars],
        },
        {
            'Classification': 'spark-env',
            'Configurations': [config_env_vars],
        },
        {
            'Classification': 'yarn-env',
            'Configurations': [config_env_vars],
        }
    ]

    client = boto3.client('emr')
    response = client.run_job_flow(
        Name='RF Tiler (test)',
        LogUri='s3://raster-foundry-tiler/logs/',
        ReleaseLabel='emr-4.0.0',

        # These roles are created when you manually launch an EMR cluster
        # or by using the following command:
        # > aws emr create-default-roles
        ServiceRole='EMR_DefaultRole',
        JobFlowRole='EMR_EC2_DefaultRole',

        Applications=[
            {
                'Name': 'Spark',
            }
        ],
        BootstrapActions=[
            {
                'Name': 'Install dependencies',
                'ScriptBootstrapAction': {
                    'Path': 's3://raster-foundry-tiler/bootstrap.sh',
                }
            }
        ],
        Instances=instances,
        Configurations=configurations,
        Steps=get_steps(),
    )
    print(response)


def get_steps():
    spark_submit = [
        'spark-submit',
        '--deploy-mode',
        'cluster',
        '--driver-memory',
        '2g',
    ]

    chunk_result = 's3://raster-foundry-tiler/step1.json'

    images = [
        's3://raster-foundry-tiler/test.tif',
    ]

    return [
        {
            'Name': 'Chunk',
            'ActionOnFailure': 'CONTINUE',
            'HadoopJarStep': {
                'Jar': 'command-runner.jar',
                'Args': spark_submit + [
                    's3://raster-foundry-tiler/chunk.py',
                    '--job-id',
                    'sample-job-id',
                    '--workspace',
                    's3://raster-foundry-tiler/workspace',
                    '--target',
                    's3://raster-foundry-tiler/result',
                    '--output',
                    chunk_result,
                    '--status-queue',
                    'http://sqs.us-east-1.amazonaws.com/123456789012/queue2',
                ] + images
            }
        },
        {
            'Name': 'Mosaic',
            'ActionOnFailure': 'CONTINUE',
            'HadoopJarStep': {
                'Jar': 'command-runner.jar',
                'Args': spark_submit + [
                    '--class',
                    'org.hotosm.oam.Main',
                    's3://raster-foundry-tiler/mosaic.jar',
                    chunk_result,
                ]
            }
        }
    ]


def add_steps(cluster_id):
    """
    Add steps to an existing cluster.
    cluster_id - Existing EMR cluster ID
    """
    client = boto3.client('emr')
    response = client.add_job_flow_steps(
        JobFlowId=cluster_id,
        Steps=get_steps(),
    )
    print(response)


if __name__ == '__main__':
    create_cluster()
