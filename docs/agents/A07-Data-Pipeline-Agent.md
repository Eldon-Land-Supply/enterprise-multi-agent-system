## A07 – Data Pipeline Agent (Microsoft Fabric / Azure Data Factory)

### Summary

The Data Pipeline Agent orchestrates heavy data preparation tasks, such as extracting, transforming and loading large datasets into OneLake or a data warehouse.  Built using Microsoft Fabric Data Pipelines or Azure Data Factory, it ensures that data is cleansed, transformed and available for the ML and OpenAI agents.  It runs both on a schedule and via API calls from the orchestrator.

### Responsibilities & Tasks

**Responsibilities**

- Connect to various data sources (e.g. SQL databases, ERP systems, CSV files) and ingest data into OneLake.
- Perform transformations (cleaning, aggregations, joining datasets) at scale.
- Load processed data into curated zones for consumption by downstream agents.
- Expose pipeline runs as an API so that A02 can trigger ad‑hoc refreshes.
- Track data lineage and register datasets in Purview.

**Concrete tasks**

1. **Nightly ETL run** – on a schedule (e.g. 01:00), extract daily transactional data from source systems, transform and load into OneLake.  Record success/failure status in Dataverse for monitoring.
2. **Incremental refresh** – triggered by A02 when new data arrives outside the regular schedule (e.g. user uploads a file).  Runs a lightweight pipeline that processes only the changed data.
3. **Data aggregation** – produces aggregated datasets (e.g. weekly totals, rolling averages) for use by the ML agent.  Runs after the nightly ETL.
4. **Data quality checks** – validates data (schema, missing values) and raises alerts to A02 if anomalies are detected.  Writes quality reports to Dataverse.

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Source systems (SQL, ERP, CSV)** | Provide raw data | Read | Native data connectors |
| **OneLake / Data Lake** | Store staging and curated data | Write & read | Fabric connectors |
| **Azure ML Agent (A03)** | Consume prepared datasets for training and scoring | Provide | Data storage |
| **Power Automate/Logic App (A02)** | Trigger ad‑hoc pipeline runs and receive status updates | Trigger & callback | REST API |
| **Purview** | Register data lineage and apply classifications | Write | Purview SDK |

### Triggers, Inputs & Outputs

**Triggers**: Scheduled recurrence for nightly loads; API calls from A02 for incremental refreshes; file upload events if connected via Event Grid.

**Inputs**: Raw data from source systems, pipeline parameters (dates, file paths).  Transformations are defined in pipeline activities.

**Outputs**: Curated datasets stored in OneLake or a data warehouse; status logs written to Dataverse or Log Analytics; data quality reports; lineage metadata in Purview.

### Error Handling & Resilience

Pipelines have built‑in retry and fault‑tolerance policies.  If extraction fails, the pipeline logs the failure and reverts partial data loads.  Data transformations are idempotent; partial runs can be resumed.  The pipeline engine scales compute resources automatically based on data volume.  Notifications of failures are sent to A02, which triggers remediation workflows.

### Security & Access

The pipeline runs under a managed identity with data-reader permissions on source systems and write permissions on OneLake/datalake.  It uses private endpoints where available.  Data is encrypted in transit.  Purview classifications are enforced to prevent sensitive data from being processed without proper handling.  All activities are logged for auditing.
