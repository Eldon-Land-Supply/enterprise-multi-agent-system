## A03 – Predictive Insights Provider (Azure Machine Learning Agent)

### Summary
              
The Predictive Insights Provider is built on Azure Machine Learning.  It trains, retrains and serves machine learning models to produce predictive scores or analytics.  It runs both scheduled batch jobs (e.g. nightly scoring of all records) and real‑time inference via REST endpoints.  The agent provides probability scores, forecasts and risk assessments that downstream agents and users consume.

### Responsibilities & Tasks

**Responsibilities**

- Build and manage ML pipelines for feature engineering, training and validation.
- Schedule regular retraining and batch scoring jobs.
- Expose REST endpoints for real‑time predictions.
- Log prediction results and model metrics to Dataverse or a data lake.
- Trigger retraining on data drift or performance degradation alerts.

**Concrete tasks**

1. **Nightly batch scoring** – triggered by a schedule (e.g. 02:00).  Extracts fresh data from the data lake or Dataverse, applies the latest model and writes predictions back to Dataverse (table `Predictions`).  Updates metrics such as model accuracy and drift indicators.
2. **Model retraining pipeline** – scheduled weekly or triggered by drift detection.  Runs a pipeline consisting of data ingestion, feature computation, model training, hyperparameter tuning and evaluation.  If the new model passes evaluation criteria, registers it in the Model Registry and deploys to an inference endpoint.
3. **Real‑time scoring** – exposed as a REST endpoint.  Receives a JSON payload of feature values from A02, returns prediction scores and confidence intervals.  Logs each request and response.
4. **Monitoring and alerts** – continuously monitors model performance metrics (latency, accuracy) and triggers A02 if thresholds are breached.  Records metrics to Application Insights and sends alerts via Azure Monitor.

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Azure ML Workspace** | Execute pipelines, manage datasets and models | Internal | SDK/REST |
| **Dataverse / OneLake** | Source and destination for training data and prediction outputs | Read & write | Python SDK / Dataverse connector |
| **Azure DevOps** | CI/CD pipeline for code and model deployment; scheduled runs | Trigger & manage | DevOps pipeline |
| **Power Automate/Logic App (A02)** | Receive calls for real‑time scoring; send back predictions | Callback | HTTP |
| **Azure Monitor & Application Insights** | Record performance metrics and alerts | Write | SDK |

### Triggers, Inputs & Outputs

**Triggers**: Scheduled time triggers for retraining and batch scoring; HTTP requests for real‑time inference; data drift events detected by monitoring.

**Inputs**: Training data from Dataverse or OneLake; hyperparameter configurations; real‑time feature payloads from A02.  Data is versioned to ensure reproducibility.

**Outputs**: Prediction tables in Dataverse (`Predictions`), model artifacts in the Model Registry, logs in Application Insights, alerts in Azure Monitor.  Real‑time responses are JSON objects containing predicted values and metadata.

### Error Handling & Resilience

Batch and retraining pipelines have built‑in retry steps for transient failures (e.g. network errors).  If a training run fails, the previous production model remains active.  Real‑time endpoint errors are surfaced back to A02 with error codes and logged.  Data pipelines use checkpointing so they can resume after failures.  Autoscaling compute clusters ensure jobs complete within SLAs.

### Security & Access

Runs under a managed identity with access to the ML Workspace, data lake and Dataverse.  Data is encrypted at rest and in transit.  The model endpoint is secured via AAD‑backed authentication; only the orchestrator identity can call it.  Access logs are recorded for auditing.  The agent adheres to Purview data policies to avoid training or serving on restricted data.
