from kfp import dsl
from kfp import compiler
from kfp import kubernetes
import argparse
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(name)s %(levelname)s:%(message)s')
logger = logging.getLogger(__name__)


def get_data_extraction_component(image: str):
    @dsl.container_component
    def data_extraction(
        s3_bucket: str,
        local_data_dir: str,
        aws_region: str,
        data_dir: str,
        new_data_dir: str
    ):
        return dsl.ContainerSpec(
            image=image,
            command=["bash", "/script/run_copy_merlin_aws.sh"],
            args=[
                s3_bucket,
                local_data_dir,
                aws_region,
                data_dir,
                new_data_dir
            ]
        )
    return data_extraction


def get_data_validation_component(image: str):
    @dsl.container_component
    def data_validation(
        local_data_dir: str,
        do_data_validation: str
    ):
        return dsl.ContainerSpec(
            image=image,
            command=["bash", "/script/run_validation.sh"],
            args=[
                local_data_dir,
                do_data_validation
            ]
        )
    return data_validation


def get_preprocess_train_component(image: str):
    @dsl.container_component
    def preprocess_train(
        local_data_dir: str,
        aws_region: str,
        s3_bucket: str,
        new_data_dir: str
    ):
        return dsl.ContainerSpec(
            image=image,
            command=["bash", "/script/preprocess-train.sh"],
            args=[
                local_data_dir,
                aws_region,
                s3_bucket,
                new_data_dir
            ]
        )
    return preprocess_train

def get_deploy_component(image: str):
    @dsl.container_component
    def deploy_triton(
        local_data_dir: str,
        aws_account_id: str,
        aws_region: str
    ):
        return dsl.ContainerSpec(
            image=image,
            command=["bash", "/script/inference/run_merlin_inference_aws.sh"],
            args=[
                local_data_dir,
                aws_account_id,
                aws_region
            ]
        )
    return deploy_triton

def get_monitoring_component(image: str):
    @dsl.container_component
    def monitoring(
        aws_region: str,
        s3_bucket: str,
        data_dir: str,
        new_data_dir: str,
        local_data_dir: str,
        pipeline_id: str,
        sqs_queue_url: str,
        aws_account_id: str,
        do_data_validation: str,
        service_account: str = 'default'
    ):
        return dsl.ContainerSpec(
            image=image,
            command=["bash", "/script/run_monitoring_aws.sh"],
            args= [
                aws_region,
                s3_bucket,
                data_dir,
                new_data_dir,
                local_data_dir,
                pipeline_id,
                sqs_queue_url,
                aws_account_id,
                do_data_validation,
                service_account
            ]
        )
    return monitoring

def create_pipeline(data_extraction_image: str, data_validation_image: str, preprocess_train_image: str, deploy_image: str, monitoring_image: str):
    
    # Get component functions with images
    data_extraction_op = get_data_extraction_component(data_extraction_image)
    data_validation_op = get_data_validation_component(data_validation_image)
    preprocess_train_op = get_preprocess_train_component(preprocess_train_image)
    deploy_op = get_deploy_component(deploy_image)
    monitoring_op = get_monitoring_component(monitoring_image)
    @dsl.pipeline(
        name="Merlin MLOPs Pipeline",
        description="HugeCTR Merlin MLOPs Pipeline on AWS EKS with Kubeflow"
    )
    def merlin_pipeline(
        data_dir: str = 'initial_data_dir_in_your-bucket',
        new_data_dir: str = 'incremental_data_dir_in_your-bucket',
        s3_bucket: str = 'your-S3-bucket',
        local_data_dir: str = '/var/lib/data',
        aws_region: str = 'aws_region, e.g. us-east-1',
        aws_account_id: str = 'your-aws-account-id',
        do_data_validation: str = 'False',
        sqs_queue_url: str = 'your-sqs-queue-url',
        pipeline_id: str = 'current-pipeline-id',
        service_account: str = 'default'
    ):
        persistent_volume_claim_name = 'merlin-pvc'
        mount_path = '/var/lib/data'

        # First component: Copy data from S3 to local storage
        copy_data_task = data_extraction_op(
            s3_bucket=s3_bucket,
            local_data_dir=local_data_dir,
            aws_region=aws_region,
            data_dir=data_dir,
            new_data_dir=new_data_dir
        ).set_caching_options(False).set_env_variable(name='HOME', value='/tmp')
        
        # Add PVC mount, node selector, and service account for copy_data
        kubernetes.mount_pvc(
            copy_data_task,
            pvc_name=persistent_volume_claim_name,
            mount_path=mount_path
        )
        kubernetes.add_node_selector(
            copy_data_task,
            label_key='nodegroup',
            label_value='cpu-node-group'
        )

        # Second component: Data validation
        validate_data_task = data_validation_op(
            local_data_dir=local_data_dir,
            do_data_validation=do_data_validation
        ).set_caching_options(False).set_env_variable(name='HOME', value='/tmp')
        
        # Add PVC mount, node selector for validate_data
        kubernetes.mount_pvc(
            validate_data_task,
            pvc_name=persistent_volume_claim_name,
            mount_path=mount_path
        )
        kubernetes.add_node_selector(
            validate_data_task,
            label_key='nodegroup',
            label_value='cpu-node-group'
        )

        # Third component: Preprocess and Train
        preprocess_train_task = preprocess_train_op(
            local_data_dir=local_data_dir,
            aws_region=aws_region,
            s3_bucket=s3_bucket,
            new_data_dir=new_data_dir
        ).set_caching_options(False).set_env_variable(name='HOME', value='/tmp')

        # Add PVC mount, node selector, GPU limit, and toleration for preprocess_train
        kubernetes.mount_pvc(
            preprocess_train_task,
            pvc_name=persistent_volume_claim_name,
            mount_path=mount_path
        )
        kubernetes.add_node_selector(
            preprocess_train_task,
            label_key='nodegroup',
            label_value='gpu-node-group'
        )
        preprocess_train_task.set_accelerator_type('nvidia.com/gpu').set_accelerator_limit(1)
        kubernetes.add_toleration(
            preprocess_train_task,
            key='nvidia.com/gpu',
            operator='Exists',
            effect='NoSchedule'
        )

        # Fourth component: Deploy (basically triggers the helm install for Triton, does not need GPU)
        deploy_task = deploy_op(
            local_data_dir=local_data_dir,
            aws_account_id=aws_account_id,
            aws_region=aws_region
        ).set_caching_options(False).set_env_variable(name='HOME', value='/tmp')
        # Add PVC mount, node selector for deploy
        kubernetes.mount_pvc(
            deploy_task,
            pvc_name=persistent_volume_claim_name,
            mount_path=mount_path
        )
        kubernetes.add_node_selector(
            deploy_task,
            label_key='nodegroup',
            label_value='cpu-node-group'
        )

        # Fifth component: Monitoring (basically runs the monitoring script that monitors accuracy and triggers the pipeline if accuracy drops, also handles live data collection and writing to S3)
        monitoring_task = monitoring_op(
            aws_region=aws_region,
            s3_bucket=s3_bucket,
            data_dir=data_dir,
            new_data_dir=new_data_dir,
            local_data_dir=local_data_dir,
            pipeline_id=pipeline_id,
            sqs_queue_url=sqs_queue_url,
            aws_account_id=aws_account_id,
            do_data_validation=do_data_validation,
            service_account=service_account
        ).set_caching_options(False).set_env_variable(name='HOME', value='/tmp')

        #add node selector for monitoring task
        kubernetes.add_node_selector(
            monitoring_task,
            label_key='nodegroup',
            label_value='cpu-node-group'
        )

        #pull policy always for all
        kubernetes.set_image_pull_policy(copy_data_task, 'Always')
        kubernetes.set_image_pull_policy(validate_data_task, 'Always')
        kubernetes.set_image_pull_policy(preprocess_train_task, 'Always')
        kubernetes.set_image_pull_policy(deploy_task, 'Always')
        kubernetes.set_image_pull_policy(monitoring_task, 'Always')

        # Define dependencies
        validate_data_task.after(copy_data_task)
        preprocess_train_task.after(validate_data_task)
        deploy_task.after(preprocess_train_task)
        monitoring_task.after(deploy_task)
    return merlin_pipeline


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-dexc", "--data_extraction_container",
        type=str,
        required=True,
        help="ECR URL data extraction container"
    )

    parser.add_argument(
        "-dvc", "--data_validation_container",
        type=str,
        required=True,
        help="ECR URL for data validation container"
    )

    parser.add_argument(
        "-ptc", "--preprocess_train_container",
        type=str,
        required=True,
        help="ECR URL for preprocess-train container"
    )
    parser.add_argument(
        "-dc", "--deploy_container",
        type=str,
        required=True,
        help="ECR URL for deploy container, same as data extraction container"
    )
    parser.add_argument(
        "-mc", "--monitoring_container",
        type=str,
        required=True,
        help="ECR URL for monitoring container"
    )

    args = parser.parse_args()

    # Log container URLs
    logger.info(f"Data Extraction Container: {args.data_extraction_container}")
    logger.info(f"Data Validation Container: {args.data_validation_container}")
    logger.info(f"Preprocess-Train Container: {args.preprocess_train_container}")
    logger.info(f"Deploy Container: {args.deploy_container}")
    logger.info(f"Monitoring Container: {args.monitoring_container}")
    
    # Create pipeline with container images
    pipeline = create_pipeline(
        data_extraction_image=args.data_extraction_container,
        data_validation_image=args.data_validation_container,
        preprocess_train_image=args.preprocess_train_container,
        deploy_image=args.deploy_container,
        monitoring_image=args.monitoring_container
    )

    # Compile the pipeline
    output_file = 'merlin-pipeline-aws.yaml'
    compiler.Compiler().compile(pipeline, output_file)
    logger.info(f"Pipeline compiled to {output_file}")