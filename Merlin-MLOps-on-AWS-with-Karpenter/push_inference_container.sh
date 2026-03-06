set -e

AWS_ACCOUNT_ID=$1
AWS_REGION=${2:-"us-east-1"}
IMAGE_TAG="0.5.1"

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
REPO_NAME="merlin/inference"

FULL_IMAGE_NAME="${ECR_REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"

#login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY

#create the ECR repository if it doesn't exist
aws ecr describe-repositories --repository-names $REPO_NAME --region $AWS_REGION >/dev/null 2>&1 || \
aws ecr create-repository --repository-name $REPO_NAME --region $AWS_REGION

#pull merlin-inference:0.5.1 from NGC container registry
docker pull nvcr.io/nvidia/merlin/merlin-inference:0.5.1

#tag the image
docker tag nvcr.io/nvidia/merlin/merlin-inference:0.5.1 $FULL_IMAGE_NAME

#push the image to ECR
docker push $FULL_IMAGE_NAME

# store the image URI for use by helm
mkdir -p .image_uris
echo "$FULL_IMAGE_NAME" > .image_uris/inference.txt
echo "Image URI saved to .image_uris/inference.txt for kubeflow pipelines use."