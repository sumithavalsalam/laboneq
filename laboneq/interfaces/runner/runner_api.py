# Copyright 2020 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from abc import ABC

from laboneq.data.execution_payload import ExecutionPayload
from laboneq.data.experiment_results import ExperimentResults


class RunnerAPI(ABC):
    def submit_execution_payload(self, job: ExecutionPayload):
        """
        Submit an experiment run job.
        """
        raise NotImplementedError

    def run_job_status(self, job_id: str):
        """
        Get the status of an  experiment run job.
        """
        raise NotImplementedError

    def run_job_result(self, job_id: str) -> ExperimentResults:
        """
        Get the result of an experiment run job. Blocks until the result is available.
        """
        raise NotImplementedError
