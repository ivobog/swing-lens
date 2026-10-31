"""Isolated adapter registration and forbidden-network regressions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("runtime_mode", ("CERTIFICATION", "NORMAL"))
def test_adapter_preserves_production_handler_and_patches_only_client_factories(
    tmp_path, runtime_mode,
):
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://unused@127.0.0.1/swinglens_pytest_cert_import_only",
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "RUNTIME_MODE": runtime_mode,
        "PROCESS_ROLE": "DURABLE_WORKER",
        "USE_DURABLE_PIPELINE": "true",
        "DURABLE_WORKER_PROCESS_ENABLED": "true",
        "EMBEDDED_JOB_WORKER_ENABLED": "false",
        "JOB_WORKER_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": "false",
        "MARKET_DATA_PREWARM_ENABLED": "false",
        "SWINGLENS_CERTIFICATION_ADAPTER_MODULE": "certification_server",
        "CERTIFICATION_FROZEN_CERI_PROVIDERS": "true",
        "CERTIFICATION_WINNER_CONFIGURATION": str(REPO_ROOT / "config/winner_probability.yaml"),
        "CERTIFICATION_OUTCOME_NOW": "2027-01-15T22:00:00+00:00",
        "CERTIFICATION_IB_LOG": str(tmp_path / "ib.jsonl"),
        "CERTIFICATION_WORKER_FIDELITY_LOG": str(tmp_path / "fidelity.jsonl"),
        "DB_MONITOR_LOG_DIR": str(tmp_path / "monitor"),
        "DB_MONITOR_TEST_LOG_DIR": str(tmp_path / "monitor-test"),
        "PYTHONPATH": os.pathsep.join((str(REPO_ROOT), str(REPO_ROOT / "tests/e2e"),
                                     str(Path(__file__).parent))),
    }
    code = """
import asyncio, json, socket
from app.services import background_worker, ib_connection, ib_fetch_executor
from app.services import ib_gateway_health_service, ib_historical_capability, pipeline_executor
from app.services.certification_adapter import install_certification_adapter
from app.settings import get_settings

handler = background_worker._execute_full_pipeline_job
services = (ib_gateway_health_service.check_status,
            ib_historical_capability.check_historical_data_capability,
            ib_fetch_executor.execute_fetch_plan,
            pipeline_executor.execute_full_pipeline)
try:
    install_certification_adapter(get_settings())
except RuntimeError as error:
    assert get_settings().runtime_mode.value == 'NORMAL'
    assert str(error) == 'CERTIFICATION_ADAPTER_REQUIRES_CERTIFICATION_RUNTIME'
    assert background_worker.default_job_handlers()['FULL_PIPELINE'] is handler
    print(json.dumps({'normal_mode_rejected': True}))
else:
    assert background_worker._execute_full_pipeline_job is handler
    assert background_worker.default_job_handlers()['FULL_PIPELINE'] is handler
    assert services == (ib_gateway_health_service.check_status,
                        ib_historical_capability.check_historical_data_capability,
                        ib_fetch_executor.execute_fetch_plan,
                        pipeline_executor.execute_full_pipeline)
    factories = (ib_connection.create_ib_client, ib_gateway_health_service.create_ib_client,
                 ib_historical_capability.create_ib_client, ib_fetch_executor.create_ib_client)
    assert len(set(factories)) == 1
    assert all(type(factory()).__name__ == 'DeterministicReadOnlyIB' for factory in factories)
    loop = asyncio.new_event_loop()
    loop.close()
    # Defaults already bound before installation must still call the native services.
    assert pipeline_executor.PipelineExecutionDependencies().check_ib_gateway().api_ready
    assert ib_gateway_health_service.check_status().api_ready
    rejected = []
    for address in (('127.0.0.1', 7497), ('203.0.113.1', 443)):
        with socket.socket() as client:
            try:
                client.connect(address)
            except RuntimeError as error:
                assert str(error) == 'CERTIFICATION_UNEXPECTED_NETWORK_ACCESS'
                rejected.append(address)
            else:
                raise AssertionError('unexpected network access allowed')
    try:
        socket.getaddrinfo('www.sec.gov', 443)
    except RuntimeError as error:
        assert str(error) == 'CERTIFICATION_UNEXPECTED_NETWORK_ACCESS'
    else:
        raise AssertionError('provider DNS resolution allowed')
    print(json.dumps({'original_handler': handler.__module__ + '.' + handler.__qualname__,
                      'factory_aliases': len(factories), 'rejected_sockets': len(rejected)}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=environment,
        text=True, capture_output=True, timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    if runtime_mode == "NORMAL":
        assert result == {"normal_mode_rejected": True}
    else:
        assert result == {
            "original_handler": "app.services.background_worker._execute_full_pipeline_job",
            "factory_aliases": 4,
            "rejected_sockets": 2,
        }
