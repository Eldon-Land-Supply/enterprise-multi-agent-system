# CI/CD Rollback Strategies

To ensure safe deployments, define rollback procedures in your CI/CD pipelines.  A rollback can be triggered manually or automatically when a deployment fails.

## Azure DevOps

Example stage template:

```yaml
# ci/azure-devops/rollback-template.yml
parameters:
  environment: 'production'
  rollback_steps:
    - script: echo "Rolling back deployment"
stages:
  - stage: Rollback
    jobs:
      - job: Rollback
        steps: ${{ parameters.rollback_steps }}
```

## GitHub Actions

Example workflow:

```yaml
# .github/workflows/rollback-example.yml
name: Rollback
on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Environment to roll back'
        required: true
        default: 'production'
jobs:
  rollback:
    runs-on: ubuntu-latest
    steps:
      - name: Rollback
        run: echo "Rolling back ${{ github.event.inputs.environment }}"
```

## Best practices

- Test rollback regularly as part of the pipeline.
- Use immutable artifacts so that the rollback action can quickly deploy the previous version.
- Record every rollback event in the audit log with the reason and initiator.
