import abc
from typing import Optional

from gravswell.quiver import Platform
from gravswell.quiver.exporters import Exporter


class EnsembleMeta(abc.ABCMeta):
    @property
    def handles(self):
        return type(None)

    @property
    def platform(self) -> Platform:
        return Platform.ENSEMBLE


class Ensemble(Exporter, metaclass=EnsembleMeta):
    def _get_output_shapes(
        self, model_fn: type(None), output_names: Optional[list[str]] = None
    ):
        shapes = {x.name: list(x.dims) for x in self.config.output}
        return shapes or None

    def export(self, model_fn, export_path):
        self.fs.write("", export_path)
