import numpy as np
import PySDM.products as PySDM_products
from PySDM import Builder
from PySDM.backends import CPU
from PySDM.dynamics import (
    AmbientThermodynamics,
    Collision,
    Condensation,
    Displacement,
    EulerianAdvection,
)
from PySDM.dynamics.collisions.breakup_efficiencies import ConstEb
from PySDM.dynamics.collisions.breakup_fragmentations import ExponFrag
from PySDM.dynamics.collisions.coalescence_efficiencies import ConstEc
from PySDM.dynamics.collisions.collision_kernels import Geometric
from PySDM.environments.kinematic_1d import Kinematic1D
from PySDM.impl.mesh import Mesh
from PySDM.initialisation.sampling import spatial_sampling, spectral_sampling
from PySDM.physics import si

from PySDM_examples.Shipway_and_Hill_2012.mpdata_1d import MPDATA_1D


class SimulationB:
    def __init__(self, settings, backend=CPU):
        self.nt = settings.nt
        self.z0 = -settings.particle_reservoir_depth

        builder = Builder(
            n_sd=settings.n_sd, backend=backend(formulae=settings.formulae)
        )
        mesh = Mesh(
            grid=(settings.nz,),
            size=(settings.z_max + settings.particle_reservoir_depth,),
        )
        env = Kinematic1D(
            dt=settings.dt,
            mesh=mesh,
            thd_of_z=settings.thd,
            rhod_of_z=settings.rhod,
            z0=-settings.particle_reservoir_depth,
        )

        self.mpdata = MPDATA_1D(
            nz=settings.nz,
            dt=settings.dt,
            mpdata_settings=settings.mpdata_settings,
            advector_of_t=lambda t: settings.rho_times_w(t) * settings.dt / settings.dz,
            advectee_of_zZ_at_t0=lambda zZ: settings.qv(zZ * settings.z_max + self.z0),
            g_factor_of_zZ=lambda zZ: settings.rhod(zZ * settings.z_max + self.z0),
        )

        _extra_nz = settings.particle_reservoir_depth // settings.dz
        self.g_factor_vec = settings.rhod(
            settings.dz
            * np.linspace(-_extra_nz, settings.nz - _extra_nz, settings.nz + 1)
        )

        builder.set_environment(env)
        builder.add_dynamic(AmbientThermodynamics())
        builder.add_dynamic(
            Condensation(
                adaptive=settings.condensation_adaptive,
                rtol_thd=settings.condensation_rtol_thd,
                rtol_x=settings.condensation_rtol_x,
            )
        )
        builder.add_dynamic(EulerianAdvection(self.mpdata))
        if settings.precip:
            builder.add_dynamic(
                Collision(
                    collision_kernel=Geometric(collection_efficiency=1),
                    coalescence_efficiency=ConstEc(Ec=0.95),
                    breakup_efficiency=ConstEb(Eb=1.0),
                    fragmentation_function=ExponFrag(scale=100 * si.um),
                    adaptive=settings.coalescence_adaptive,
                )
            )
        displacement = Displacement(enable_sedimentation=True)
        builder.add_dynamic(displacement)
        attributes = env.init_attributes(
            spatial_discretisation=spatial_sampling.Pseudorandom(),
            spectral_discretisation=spectral_sampling.ConstantMultiplicity(
                spectrum=settings.wet_radius_spectrum_per_mass_of_dry_air
            ),
            kappa=settings.kappa,
        )
        products = [
            PySDM_products.AmbientRelativeHumidity(name="RH", unit="%"),
            PySDM_products.AmbientPressure(name="p"),
            PySDM_products.AmbientTemperature(name="T"),
            PySDM_products.AmbientWaterVapourMixingRatio(name="qv"),
            PySDM_products.WaterMixingRatio(
                name="ql", unit="g/kg", radius_range=settings.cloud_water_radius_range
            ),
            PySDM_products.WaterMixingRatio(
                name="qr", unit="g/kg", radius_range=settings.rain_water_radius_range
            ),
            PySDM_products.AmbientDryAirDensity(name="rhod"),
            PySDM_products.AmbientDryAirPotentialTemperature(name="thd"),
            PySDM_products.ParticleSizeSpectrumPerVolume(
                name="dry spectrum", radius_bins_edges=settings.r_bins_edges, dry=True
            ),
            PySDM_products.ParticleSizeSpectrumPerVolume(
                name="wet spectrum", radius_bins_edges=settings.r_bins_edges
            ),
            PySDM_products.ParticleConcentration(
                name="nc", radius_range=settings.cloud_water_radius_range
            ),
            PySDM_products.ParticleConcentration(
                name="na", radius_range=(0, settings.cloud_water_radius_range[0])
            ),
            PySDM_products.MeanRadius(),
            PySDM_products.RipeningRate(),
            PySDM_products.ActivatingRate(),
            PySDM_products.DeactivatingRate(),
            PySDM_products.EffectiveRadius(
                radius_range=settings.cloud_water_radius_range
            ),
            PySDM_products.PeakSupersaturation(unit="%"),
            PySDM_products.SuperDropletCountPerGridbox(),
        ]
        self.particulator = builder.build(attributes=attributes, products=products)

    def save(self, output, step):
        for k, v in self.particulator.products.items():
            if len(v.shape) == 1:
                output[k][:, step] = v.get()

    def run(self, nt=None):
        nt = self.nt if nt is None else nt
        mesh = self.particulator.mesh

        output = {
            k: np.zeros((mesh.grid[-1], nt + 1)) for k in self.particulator.products
        }
        assert "t" not in output and "z" not in output
        output["t"] = np.linspace(
            0, self.nt * self.particulator.dt, self.nt + 1, endpoint=True
        )
        output["z"] = np.linspace(
            self.z0 + mesh.dz / 2,
            self.z0 + (mesh.grid[-1] - 1 / 2) * mesh.dz,
            mesh.grid[-1],
            endpoint=True,
        )

        self.save(output, 0)
        for step in range(nt):
            if "Displacement" in self.particulator.dynamics:
                self.particulator.dynamics["Displacement"].upload_courant_field(
                    (self.mpdata.advector / self.g_factor_vec,)
                )
            self.particulator.run(steps=1)
            self.save(output, step + 1)
        return output