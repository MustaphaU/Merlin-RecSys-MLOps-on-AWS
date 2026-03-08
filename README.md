# **Recommender System with Continuous Retraining on Amazon EKS with NVIDIA Merlin, HugeCTR, NVIDIA Triton Inference server, and Kubeflow Pipelines**

![Architecture Diagram](images/arch___.png
)

This project is a deep learning based recommender system with continuous retraining. The recommendation model predicts Click Through Rates (CTR) and automatically retrains when performance degrades. This set up utilizes technologies including Amazon Elastic Kubernetes Service (EKS), NVIDIA Triton Inference Server, NVIDIA Merlin (NVTabular, HugeCTR), and Kubeflow Pipelines. 

* [Amazon Elastic Kubernetes Service (EKS)](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html) is a fully managed Kubernetes service that can scale nodes to meet changing workload demands.

* [NVIDIA Triton Inference Server](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/index.html) is an open source inference serving software that enables the deployment of AI/ML models from frameworks including HugeCTR, PyTorch, TensorFlow, TensorRT, et cetera.

* [NVIDIA Merlin](https://developer.nvidia.com/merlin) is an open source framework for building recommender systems at scale.

* [Kubeflow Pipelines](https://www.kubeflow.org/docs/components/pipelines/overview/) (KFP) is an open source platform for writing machine learning workflows natively in Python and deploying them on Kubernetes-based systems.

## Deployment Instructions
There are two variants of this project based on the autoscaling product. 
1. ### **Autoscaling with Karpenter (self-managed) and Horizontal Pod Autoscaler**  
    Triton pods are scaled using Kubernetes HPA with Custom metrics and the Cluster Nodes are managed/scaled by Karpenter. The codes are in the directory [Merlin-MLOps-on-AWS-with-Karpenter](Merlin-MLOps-on-AWS-with-Karpenter), also visit [SETUP_INSTRUCTIONS](Merlin-MLOps-on-AWS-with-Karpenter/documentation/setup_instructions.md) for instructions on how to set up the infrastructure and deploy the recommender system.
    ![Autoscaling with Karpenter and k8s HPA](images/Karpenter_autoscaling.png)

2. ### **Autoscaling with Cluster Autoscaler and Horizontal Pod Autoscaler (HPA)**  
    Triton pods are scaled using the Kubernetes HPA with Custom metrics and Cluster nodes are scaled by Cluster Autoscaler. The codes are in the directory [Merlin-MLOps-on-AWS-with-with-Cluster_Autoscaler](Merlin-MLOps-on-AWS-with-with-Cluster_Autoscaler), also visit [SETUP_INSTRUCTIONS](Merlin-MLOps-on-AWS-with-with-Cluster_Autoscaler/documentation/SETUP_INSTRUCTIONS.md) for instructions on how to set up the infrastructure and deploy the recommender system.
    ![Autoscaling with Cluster Autoscaler and k8s HPA](images/Cluster_Autoscaling.png)

## Acknowledgements
This was been inspired by [Merlin MLOps with Kubeflow Pipelines on Google Kubernetes Engine](https://developer.nvidia.com/blog/continuously-improving-recommender-systems-for-competitive-advantage-with-merlin-and-mlops/); in fact, it is an adaptation of the [Merlin - MLOps on GKE project on GitHub](https://github.com/NVIDIA-Merlin/gcp-ml-ops?tab=readme-ov-file#merlin---mlops-on-gke) for deployment on Amazon EKS. Therefore, you will find that some of the ideas in the referenced project are replicated in this implementation and most of the code has been reused but updated to use AWS and to work with updated SDKs.

## References

1. [Continuously Improving Recommender Systems for Competitive Advantage Using NVIDIA Merlin and MLOps by Shashank Verma, Abhishek Sawarkar, Vinh Nguyen, and Davide Onofrio](https://developer.nvidia.com/blog/continuously-improving-recommender-systems-for-competitive-advantage-with-merlin-and-mlops/)
2. [Merlin - MLOps on GKE](https://github.com/NVIDIA-Merlin/gcp-ml-ops?tab=readme-ov-file#merlin---mlops-on-gke)