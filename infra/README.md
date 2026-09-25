# AWS infrastructure

One CloudFormation stack, `rpg-foundation`, in **ap-south-1** (Mumbai). It holds the
always-free AWS footprint for this project: object storage, a job queue, and one scoped
IAM identity for the Render-hosted backend.

**The backend stays on Render.** Nothing here runs compute. That is a deliberate cost
decision, not an oversight — see [Why no EC2](#why-no-ec2).

## What exists

| Resource | Physical name | Purpose |
| --- | --- | --- |
| S3 bucket | `rpg-foundation-uploads-975779326279` | Uploaded PDFs (`uploads/`) and generated export bundles (`exports/`) |
| SQS queue | `rpg-foundation-research-jobs` | Corpus-preparation jobs, decoupled from the web process |
| SQS DLQ | `rpg-foundation-research-jobs-dlq` | Jobs that failed three deliveries |
| IAM user | `rpg-foundation-render-backend` | The identity Render authenticates as |
| Managed policy | `rpg-foundation-backend-access` | Least-privilege S3 + SQS + SSM for that user |

Stack outputs carry the bucket name, both queue URLs and the queue ARN. Read them with:

```bash
aws cloudformation describe-stacks --stack-name rpg-foundation --region ap-south-1 \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text
```

## Deploy

```bash
# validate first - cfn-lint catches schema errors, not service constraints
python -c "from cfnlint import api; \
  [print(m) for m in api.lint_all(open('infra/foundation.yaml').read())]"

aws cloudformation deploy \
  --stack-name rpg-foundation \
  --template-file infra/foundation.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-south-1 \
  --tags project=research-paper-guide managed-by=cloudformation
```

## Backend credentials

Render runs outside AWS and cannot assume a role, so the backend needs a static key pair.
**The template deliberately does not create one** — `AWS::IAM::AccessKey` would publish the
secret into stack outputs in plaintext, readable by anyone with `describe-stacks`.

Create it yourself, and paste the result straight into Render's environment settings:

```bash
aws iam create-access-key --user-name rpg-foundation-render-backend
```

Set on Render: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION=ap-south-1`.
The secret is shown **once**. If it leaks, rotate immediately:

```bash
aws iam list-access-keys --user-name rpg-foundation-render-backend
aws iam delete-access-key --user-name rpg-foundation-render-backend --access-key-id <OLD>
```

## API keys in Parameter Store

CloudFormation cannot create SecureString parameters, so these are CLI-only. The backend
policy grants read access to `/rpg/*` and nothing else. One parameter per key:

```bash
aws ssm put-parameter --name /rpg/GEMINI_API_KEY --type SecureString \
  --value '<paste>' --region ap-south-1
```

Parameter Store Standard is free; Secrets Manager would bill $0.40/secret/month, which for
the ~15 keys in `backend/.env.example` is ~$6/month for no added capability here.

## Cost

The account runs on the credits-based **FREE** plan: $100, expiring **2027-02-18**. A
`research-paper-guide-monthly` budget ($20/mo, alerts at 25/50/100% actual and 100%
forecast) tracks **gross** spend (`IncludeCredit: false`) so it measures credit burn rather
than net-zero charges.

Everything in this stack is per-use or free:

- **S3** — a few cents per month at this volume. Lifecycle rules abort incomplete
  multipart uploads after 7 days, expire noncurrent versions after 30, and delete
  `exports/` objects after 30, so storage cannot grow unbounded.
- **SQS** — 1M requests/month always free.
- **SSM Parameter Store (Standard)** — free.
- **IAM** — free.

### Why no EC2

An always-on `t4g.small` plus its public IPv4 and EBS volume runs ~$19.70/month, or ~$109
across the credit window — more than the entire balance, burning 24/7 whether or not anyone
uses the app. Render already provides the backend host for free. AWS is used here only for
what Render cannot do: durable object storage, a queue that survives a redeploy, and
(next) an isolated LaTeX compile.

## Teardown

```bash
aws cloudformation delete-stack --stack-name rpg-foundation --region ap-south-1
```

**The bucket survives on purpose.** It carries `DeletionPolicy: Retain` because it holds
user documents. After deleting the stack, remove it explicitly once you are certain:

```bash
aws s3 rm s3://rpg-foundation-uploads-975779326279 --recursive
aws s3api delete-bucket --bucket rpg-foundation-uploads-975779326279 --region ap-south-1
```

## Gotchas hit while building this

Both cost a deploy cycle; neither is caught by `cfn-lint`.

- **SQS policies take exactly one resource per statement.** A single `QueuePolicy` listing
  both queue ARNs is rejected at create time with *"Each statement in the policy should
  have exactly one resource"*. Hence two separate policy resources. A shared document would
  have been wrong anyway — it attaches a statement naming the primary queue onto the DLQ.
- **`DeletionPolicy: Retain` applies to create-rollback, not just stack deletion.** When
  the first deploy failed, the bucket was retained and orphaned, and every retry then
  collided on the bucket name. If a create fails, check for a surviving bucket before
  redeploying.

## Wiring status

**The bucket is wired (AWS-6).** `backend/core/object_store.py` writes uploads to S3 when
`S3_BUCKET` is set and to GridFS when it is not. The code is shipped but **switched off**:
nothing reaches S3 until that variable is set on Render.

To turn it on, add to Render's environment (the IAM keys are already there):

```
S3_BUCKET=rpg-foundation-uploads-975779326279
AWS_REGION=ap-south-1
```

Unset it to go back. Files written while it was on stay readable either way — reads
dispatch on the shape of the stored id. Note that existing GridFS PDFs are **not**
migrated, so Atlas stops growing rather than shrinking; a backfill is not written.

Locally, use your own session rather than the Render keys:

```
AWS_PROFILE=personal
pip install "botocore[crt]"    # boto3 cannot read an `aws login` profile without it
```

Still open:

- **AWS-7** — publish research jobs to SQS (`services/research_jobs.py`). The queue exists
  and is unused.
- **AWS-8** — a Lambda container running Tectonic for **2.9** (the camera-ready PDF).
  Needs Docker locally to build the image — not currently installed.
