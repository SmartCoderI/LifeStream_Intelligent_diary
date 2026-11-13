"""F7 infrastructure tests: terraform validate, tflint, checkov, and checks on the plan JSON.

The plan runs offline (fake credentials, fixed account / VPC IDs), so these tests need no AWS
access. They need `terraform` (and tflint / checkov for those two tests); each skips if missing.
`terraform init` needs network once to download the AWS provider.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"
ACCOUNT = "123456789012"
REGION = "us-west-1"
BUCKET = f"lifestream-data-{ACCOUNT}-{REGION}"
BUCKET_ARN = f"arn:aws:s3:::{BUCKET}"

needs_terraform = pytest.mark.skipif(shutil.which("terraform") is None, reason="needs terraform")


def _offline_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_CONFIG_FILE=os.devnull,
               AWS_SHARED_CREDENTIALS_FILE=os.devnull, TF_IN_AUTOMATION="1")
    return env


def _tf(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["terraform", f"-chdir={INFRA}", *args, "-no-color"], env=_offline_env(),
                          capture_output=True, text=True, **kw)


@pytest.fixture(scope="session")
def initialized():
    if shutil.which("terraform") is None:
        pytest.skip("needs terraform")
    if not (INFRA / ".terraform").exists():
        result = _tf("init", "-backend=false", "-input=false")
        if result.returncode != 0:
            pytest.skip(f"terraform init failed (offline?): {result.stderr[-500:]}")


def _plan(tmp_path_factory, *extra: str) -> dict:
    out = tmp_path_factory.mktemp("tf") / "test.tfplan"
    result = _tf("plan", "-input=false", "-lock=false", "-refresh=false", f"-out={out}",
                 "-var", "offline_plan=true", "-var", f"account_id={ACCOUNT}", "-var", f"region={REGION}",
                 "-var", "vpc_id=vpc-0123456789abcdef0", "-var", 'subnet_ids=["subnet-0123456789abcdef0"]',
                 *extra)
    assert result.returncode == 0, result.stderr
    shown = _tf("show", "-json", str(out))
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)


@pytest.fixture(scope="session")
def plan(initialized, tmp_path_factory) -> dict:
    return _plan(tmp_path_factory)


@pytest.fixture(scope="session")
def spot_plan(initialized, tmp_path_factory) -> dict:
    return _plan(tmp_path_factory, "-var", "use_spot=true")


def _resources(plan: dict, rtype: str) -> list[dict]:
    return [r["values"] for r in plan["planned_values"]["root_module"]["resources"] if r["type"] == rtype]


def _one(plan: dict, rtype: str) -> dict:
    found = _resources(plan, rtype)
    assert len(found) == 1, f"expected one {rtype}, found {len(found)}"
    return found[0]


def _statements(policy_json: str) -> list[dict]:
    statements = json.loads(policy_json)["Statement"]
    return statements if isinstance(statements, list) else [statements]


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _inline_policies(plan: dict) -> dict[str, list[dict]]:
    return {p["role"] if isinstance(p.get("role"), str) else p["name"]: _statements(p["policy"])
            for p in _resources(plan, "aws_iam_role_policy")}


def _policy_named(plan: dict, name: str) -> list[dict]:
    for p in _resources(plan, "aws_iam_role_policy"):
        if p["name"] == name:
            return _statements(p["policy"])
    raise AssertionError(f"no inline policy {name}")


# ---------- static checks ----------

def test_terraform_validate(initialized):
    result = _tf("validate")
    assert result.returncode == 0, result.stdout + result.stderr


def test_terraform_fmt(initialized):
    result = _tf("fmt", "-check", "-recursive")
    assert result.returncode == 0, f"run `terraform fmt` in infra/: {result.stdout}"


@pytest.mark.skipif(shutil.which("tflint") is None, reason="needs tflint")
def test_tflint():
    init = subprocess.run(["tflint", "--init"], cwd=INFRA, capture_output=True, text=True)
    if init.returncode != 0:
        pytest.skip(f"tflint --init failed (offline?): {init.stderr[-300:]}")
    result = subprocess.run(["tflint", "--no-color"], cwd=INFRA, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("checkov") is None, reason="needs checkov")
def test_checkov():
    result = subprocess.run(["checkov", "-d", str(INFRA), "--framework", "terraform", "--quiet",
                             "--compact", "--skip-download", "-o", "json"],
                            capture_output=True, text=True)
    report = json.loads(result.stdout)
    report = report[0] if isinstance(report, list) else report
    failed = [f"{c['check_id']} {c['resource']}: {c['check_name']}" for c in report["results"]["failed_checks"]]
    assert not failed, "\n".join(failed)


# ---------- S3: the bucket isn't public ----------

def test_bucket_blocks_all_public_access(plan):
    block = _one(plan, "aws_s3_bucket_public_access_block")
    for setting in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
        assert block[setting] is True, setting


def test_bucket_has_no_acls_and_is_encrypted(plan):
    assert _one(plan, "aws_s3_bucket")["bucket"] == BUCKET
    ownership = _one(plan, "aws_s3_bucket_ownership_controls")
    assert ownership["rule"][0]["object_ownership"] == "BucketOwnerEnforced"
    sse = _one(plan, "aws_s3_bucket_server_side_encryption_configuration")
    assert sse["rule"][0]["apply_server_side_encryption_by_default"][0]["sse_algorithm"] == "aws:kms"
    assert _one(plan, "aws_s3_bucket_versioning")["versioning_configuration"][0]["status"] == "Enabled"


def test_bucket_policy_grants_nothing_and_requires_https(plan):
    statements = _statements(_one(plan, "aws_s3_bucket_policy")["policy"])
    assert all(s["Effect"] == "Deny" for s in statements), "bucket policy must not Allow anyone"
    https = [s for s in statements if s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"]
    assert https, "missing the deny-HTTP statement"
    assert set(_as_list(https[0]["Resource"])) == {BUCKET_ARN, f"{BUCKET_ARN}/*"}


# ---------- IAM: permissions limited to the right resources ----------

STAR_ALLOWED = {"ecr:GetAuthorizationToken"}  # AWS doesn't support resource-level permissions here


def test_no_wildcard_actions_or_unscoped_resources(plan):
    for role, statements in _inline_policies(plan).items():
        for s in statements:
            actions = _as_list(s["Action"])
            assert s["Effect"] == "Allow"
            assert not any(a == "*" or a.endswith(":*") for a in actions), f"{role}: wildcard action {actions}"
            if "*" in _as_list(s["Resource"]):
                assert set(actions) <= STAR_ALLOWED, f"{role}: {actions} on Resource '*'"


def test_job_role_reads_uploads_and_writes_results_only(plan):
    statements = _policy_named(plan, "read-uploads-write-results")
    grants = {(a, r) for s in statements for a in _as_list(s["Action"]) for r in _as_list(s["Resource"])}
    assert grants == {("s3:GetObject", f"{BUCKET_ARN}/uploads/*"), ("s3:PutObject", f"{BUCKET_ARN}/out/*")}


def test_execution_role_is_scoped(plan):
    statements = {s["Sid"]: s for s in _policy_named(plan, "start-worker-container")}
    assert set(statements) == {"EcrLogin", "PullWorkerImage", "WriteWorkerLogs", "ReadAnthropicKey"}
    assert statements["PullWorkerImage"]["Resource"] == \
        f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/lifestream-worker"
    log_group = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/batch/lifestream-worker"
    assert set(_as_list(statements["WriteWorkerLogs"]["Resource"])) == {log_group, f"{log_group}:*"}
    assert statements["ReadAnthropicKey"]["Action"] == "ssm:GetParameters"
    assert statements["ReadAnthropicKey"]["Resource"] == \
        f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/lifestream/anthropic-api-key"


def test_build_role_is_scoped(plan):
    statements = {s["Sid"]: s for s in _policy_named(plan, "build-and-push-worker-image")}
    assert set(statements) == {"ReadBuildSource", "LocateBucket", "EcrLogin", "PushWorkerImage",
                               "WriteBuildLogs", "ReadHfToken"}
    assert statements["ReadBuildSource"]["Resource"] == f"{BUCKET_ARN}/build/*"
    assert statements["LocateBucket"]["Resource"] == BUCKET_ARN
    assert statements["PushWorkerImage"]["Resource"] == \
        f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/lifestream-worker"
    assert statements["ReadHfToken"]["Resource"] == f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/lifestream/hf-token"


def test_build_project(plan):
    project = _one(plan, "aws_codebuild_project")
    env = project["environment"][0]
    assert env["privileged_mode"] is True                       # Docker inside CodeBuild
    variables = {v["name"]: v for v in env["environment_variable"]}
    assert variables["HF_TOKEN"]["type"] == "PARAMETER_STORE"   # never plain text
    assert variables["HF_TOKEN"]["value"] == "/lifestream/hf-token"
    assert project["source"][0]["location"] == f"{BUCKET}/build/source.zip"


def test_trust_policies(plan):
    roles = {r["name"]: _statements(r["assume_role_policy"]) for r in _resources(plan, "aws_iam_role")}
    assert set(roles) == {"lifestream-worker-execution", "lifestream-worker-job", "lifestream-batch-instance",
                          "lifestream-image-build"}
    (s,) = roles["lifestream-image-build"]
    assert s["Principal"] == {"Service": "codebuild.amazonaws.com"}
    assert s["Condition"]["StringEquals"]["aws:SourceAccount"] == ACCOUNT
    for name in ("lifestream-worker-execution", "lifestream-worker-job"):
        (s,) = roles[name]
        assert s["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
        assert s["Condition"]["StringEquals"]["aws:SourceAccount"] == ACCOUNT  # confused-deputy guard
    (s,) = roles["lifestream-batch-instance"]
    assert s["Principal"] == {"Service": "ec2.amazonaws.com"}
    assert all(st["Action"] == "sts:AssumeRole" for st in sum(roles.values(), []))


def test_only_expected_managed_policies(plan):
    attached = {a["policy_arn"] for a in _resources(plan, "aws_iam_role_policy_attachment")}
    assert attached == {"arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"}


# ---------- cost ----------

def test_lowest_cost_settings(plan):
    import json as _json
    rules = _json.loads(_one(plan, "aws_ecr_lifecycle_policy")["policy"])["rules"]
    assert rules[0]["selection"]["countNumber"] == 1                 # one image stored
    lifecycle = _one(plan, "aws_s3_bucket_lifecycle_configuration")["rule"][0]
    assert lifecycle["noncurrent_version_expiration"][0]["noncurrent_days"] == 1
    assert {g["retention_in_days"] for g in _resources(plan, "aws_cloudwatch_log_group")} == {30}
    params = _resources(plan, "aws_ssm_parameter")
    assert {p["name"] for p in params} == {"/lifestream/anthropic-api-key", "/lifestream/hf-token"}
    assert all(p["type"] == "SecureString" and p["tier"] == "Standard" for p in params)  # free tier
    assert not _resources(plan, "aws_secretsmanager_secret")
    assert _one(plan, "aws_codebuild_project")["environment"][0]["compute_type"] == "BUILD_GENERAL1_SMALL"
    assert _one(plan, "aws_batch_compute_environment")["compute_resources"][0]["max_vcpus"] == 4


# ---------- ECR and Batch ----------

def test_ecr_repository(plan):
    repo = _one(plan, "aws_ecr_repository")
    assert repo["image_tag_mutability"] == "IMMUTABLE"
    assert repo["image_scanning_configuration"][0]["scan_on_push"] is True


def test_on_demand_by_default(plan):
    (env,) = _resources(plan, "aws_batch_compute_environment")
    assert env["name"] == "lifestream-gpu" and env["compute_resources"][0]["type"] == "EC2"
    assert plan["variables"]["use_spot"]["value"] is False


def test_spot_switch(spot_plan):
    envs = {e["name"]: e["compute_resources"][0] for e in _resources(spot_plan, "aws_batch_compute_environment")}
    spot = envs["lifestream-gpu-spot"]
    assert spot["type"] == "SPOT" and spot["allocation_strategy"] == "SPOT_PRICE_CAPACITY_OPTIMIZED"
    assert spot["min_vcpus"] == 0 and spot["instance_type"] == ["g4dn.xlarge"]
    assert spot["ec2_configuration"][0]["image_type"] == "ECS_AL2023_NVIDIA"
    assert spot_plan["planned_values"]["outputs"]["compute_environment"]["value"] == "lifestream-gpu-spot"


def test_jobs_retry_only_when_the_instance_is_taken_away(plan):
    strategy = _one(plan, "aws_batch_job_definition")["retry_strategy"][0]
    assert strategy["attempts"] == 3
    rules = [(r["action"].upper(), r.get("on_status_reason") or None, r.get("on_reason") or None)
             for r in strategy["evaluate_on_exit"]]
    assert rules[0] == ("RETRY", "Host EC2*", None)       # Spot reclaim / host failure
    assert rules[-1][0] == "EXIT" and rules[-1][2] == "*"  # anything else fails straight away


def test_gpu_compute_environment_scales_to_zero(plan):
    env = _one(plan, "aws_batch_compute_environment")
    resources = env["compute_resources"][0]
    assert resources["min_vcpus"] == 0
    assert resources["instance_type"] == ["g4dn.xlarge"]
    assert resources["ec2_configuration"][0]["image_type"] == "ECS_AL2023_NVIDIA"
    template = _one(plan, "aws_launch_template")
    assert template["metadata_options"][0]["http_tokens"] == "required"
    assert template["block_device_mappings"][0]["ebs"][0]["encrypted"] == "true"


def test_worker_security_group_has_no_inbound(plan):
    sg = _one(plan, "aws_security_group")
    assert sg["ingress"] == []
    for separate in ("aws_security_group_rule", "aws_vpc_security_group_ingress_rule"):
        assert not _resources(plan, separate)
    assert [(r["from_port"], r["to_port"]) for r in sg["egress"]] == [(443, 443)]


def test_job_definition(plan):
    job = _one(plan, "aws_batch_job_definition")
    props = json.loads(job["container_properties"])
    assert props["image"] == f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/lifestream-worker:v1"
    assert props["command"][:2] == ["worker", "Ref::input"]
    assert {r["type"]: r["value"] for r in props["resourceRequirements"]}["GPU"] == "1"
    assert props["jobRoleArn"].endswith(":role/lifestream-worker-job")
    assert props["executionRoleArn"].endswith(":role/lifestream-worker-execution")
    assert props["secrets"] == [{"name": "ANTHROPIC_API_KEY", "valueFrom":
                                 f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/lifestream/anthropic-api-key"}]
    assert not any(e["name"] == "ANTHROPIC_API_KEY" for e in props.get("environment", []))
    assert job["parameters"]["output"] == f"s3://{BUCKET}/out/"
    assert {"name": "LLM_MODEL", "value": "claude-opus-5-5"} in props["environment"]
