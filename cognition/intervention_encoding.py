"""Declared software-workload input port for the unchanged spiking circuit."""

import numpy as np

from cognition.task_brain import TaskLearningBrain

ENCODING = "sqlite-query-histogram-v1"


class InterventionBrain(TaskLearningBrain):
    @staticmethod
    def encode(event):
        context = np.asarray(event.get("context"), dtype=float)
        if (
            context.shape != (7,)
            or not np.isfinite(context).all()
            or np.any(context < 0)
            or np.any(context > 1)
            or not np.isclose(context[:3].sum(), 1)
        ):
            raise ValueError("A visible bounded query-workload context is required")
        values = np.empty(128)
        for group in range(3):
            values[group * 32 : (group + 1) * 32] = (
                0.9 * context[group] + 0.25 * context[-1]
            )
        values[96:128] = context[-1] * 0.65
        return values
