"""Small non-ring ns-3 topologies derived from all-to-all hardware profiles."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import HardwareProfile


@dataclass(frozen=True)
class TopologyLink:
    src: int
    dst: int
    bandwidth_gbps: float
    latency_ns: float

    def __post_init__(self) -> None:
        if self.src < 0 or self.dst < 0 or self.src == self.dst:
            raise ValueError("topology link endpoints are invalid")
        if self.bandwidth_gbps <= 0 or self.latency_ns < 0:
            raise ValueError("topology rates must be positive and latency non-negative")


@dataclass(frozen=True)
class Ns3Topology:
    gpu_ids: tuple[int, ...]
    nvswitch_ids: tuple[int, ...]
    tor_id: int | None
    links: tuple[TopologyLink, ...]
    gpus_per_server: int
    gpu_type: str = "H100"
    switch_ids: tuple[int, ...] = ()
    tor_ids: tuple[int, ...] = ()
    gateway_ids: tuple[int, ...] = ()
    spine_ids: tuple[int, ...] = ()
    background_ids: tuple[int, ...] = ()

    @property
    def regular_switch_ids(self) -> tuple[int, ...]:
        if self.switch_ids:
            return self.switch_ids
        return (self.tor_id,) if self.tor_id is not None else ()

    @property
    def node_count(self) -> int:
        return len(self.gpu_ids) + len(self.nvswitch_ids) + len(self.regular_switch_ids) + len(self.background_ids)

    def serialize(self) -> str:
        switch_count = len(self.regular_switch_ids)
        first = (
            f"{self.node_count} {self.gpus_per_server} {len(self.nvswitch_ids)} "
            f"{switch_count} {len(self.links)} {self.gpu_type}"
        )
        special_nodes = [*self.nvswitch_ids, *self.regular_switch_ids]
        lines = [first, " ".join(str(node) for node in special_nodes)]
        for link in self.links:
            bandwidth = f"{link.bandwidth_gbps:.12g}Gbps"
            latency = f"{link.latency_ns / 1_000_000:.12g}ms"
            lines.append(f"{link.src} {link.dst} {bandwidth} {latency} 0")
        return "\n".join(lines) + "\n"


def build_topology(
    hardware: HardwareProfile,
    *,
    expected_num_devices: int | None = None,
) -> Ns3Topology:
    if expected_num_devices is not None and expected_num_devices != hardware.num_devices:
        raise ValueError("device count does not match the hardware profile")
    gpu_ids = tuple(range(hardware.num_devices))
    nvswitch_ids = tuple(
        range(hardware.num_devices, hardware.num_devices + hardware.num_servers)
    )
    tor_id = hardware.num_devices + hardware.num_servers if hardware.num_servers > 1 else None
    if hardware.intra.beta_ns_per_byte > 0:
        intra_bandwidth = 8.0 / hardware.intra.beta_ns_per_byte
    else:
        intra_bandwidth = hardware.link_bandwidth_gbps * hardware.gpus_per_server
    uplink_bandwidth = (
        hardware.link_bandwidth_gbps
        * hardware.gpus_per_server
        / hardware.oversubscription
    )
    links: list[TopologyLink] = []
    for gpu_id in gpu_ids:
        server_id = gpu_id // hardware.gpus_per_server
        links.append(
            TopologyLink(
                gpu_id,
                nvswitch_ids[server_id],
                intra_bandwidth,
                hardware.intra.alpha_ns,
            )
        )
    if tor_id is not None:
        for nvswitch_id in nvswitch_ids:
            links.append(
                TopologyLink(
                    nvswitch_id,
                    tor_id,
                    uplink_bandwidth,
                    hardware.inter.alpha_ns,
                )
            )
    return Ns3Topology(
        gpu_ids=gpu_ids,
        nvswitch_ids=nvswitch_ids,
        tor_id=tor_id,
        links=tuple(links),
        gpus_per_server=hardware.gpus_per_server,
        switch_ids=(tor_id,) if tor_id is not None else (),
        tor_ids=(tor_id,) if tor_id is not None else (),
    )


def build_multidc_topology(
    hardware: HardwareProfile,
    *,
    data_centers: int,
    servers_per_data_center: int,
    intra_dc_bandwidth_gbps: float,
    intra_dc_latency_ns: float,
    inter_dc_bandwidth_gbps: float,
    inter_dc_latency_ns: float,
    expected_num_devices: int | None = None,
) -> Ns3Topology:
    """Build full-mesh data centers with one ToR and gateway per DC.

    All latency inputs are one-way per-link delays, matching ns-3's
    point-to-point channel delay semantics.
    """
    if expected_num_devices is not None and expected_num_devices != hardware.num_devices:
        raise ValueError("device count does not match the hardware profile")
    if data_centers < 2 or servers_per_data_center < 1:
        raise ValueError("data-center counts must be positive and include at least two DCs")
    if data_centers * servers_per_data_center != hardware.num_servers:
        raise ValueError("data-center layout does not match the hardware server count")
    if intra_dc_bandwidth_gbps <= 0 or inter_dc_bandwidth_gbps <= 0:
        raise ValueError("data-center link bandwidths must be positive")
    if intra_dc_latency_ns < 0 or inter_dc_latency_ns < 0:
        raise ValueError("data-center link latencies must be non-negative")

    gpu_ids = tuple(range(hardware.num_devices))
    nvswitch_ids = tuple(
        range(hardware.num_devices, hardware.num_devices + hardware.num_servers)
    )
    first_switch_id = hardware.num_devices + hardware.num_servers
    tor_ids = tuple(range(first_switch_id, first_switch_id + data_centers))
    gateway_ids = tuple(
        range(first_switch_id + data_centers, first_switch_id + 2 * data_centers)
    )
    if hardware.intra.beta_ns_per_byte > 0:
        machine_bandwidth = 8.0 / hardware.intra.beta_ns_per_byte
    else:
        machine_bandwidth = hardware.link_bandwidth_gbps * hardware.gpus_per_server

    links: list[TopologyLink] = []
    for gpu_id in gpu_ids:
        server_id = gpu_id // hardware.gpus_per_server
        links.append(
            TopologyLink(
                gpu_id,
                nvswitch_ids[server_id],
                machine_bandwidth,
                hardware.intra.alpha_ns,
            )
        )
    for server_id, nvswitch_id in enumerate(nvswitch_ids):
        data_center_id = server_id // servers_per_data_center
        links.append(
            TopologyLink(
                nvswitch_id,
                tor_ids[data_center_id],
                intra_dc_bandwidth_gbps,
                intra_dc_latency_ns,
            )
        )
    for tor_id, gateway_id in zip(tor_ids, gateway_ids):
        links.append(
            TopologyLink(
                tor_id,
                gateway_id,
                intra_dc_bandwidth_gbps,
                intra_dc_latency_ns,
            )
        )
    for source_index, source in enumerate(gateway_ids):
        for destination in gateway_ids[source_index + 1 :]:
            links.append(
                TopologyLink(
                    source,
                    destination,
                    inter_dc_bandwidth_gbps,
                    inter_dc_latency_ns,
                )
            )
    return Ns3Topology(
        gpu_ids=gpu_ids,
        nvswitch_ids=nvswitch_ids,
        tor_id=tor_ids[0],
        links=tuple(links),
        gpus_per_server=hardware.gpus_per_server,
        switch_ids=(*tor_ids, *gateway_ids),
        tor_ids=tor_ids,
        gateway_ids=gateway_ids,
    )


def write_topology(
    hardware: HardwareProfile,
    path: Path,
    *,
    expected_num_devices: int | None = None,
    topology_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if topology_config is None:
        kind = "single-tor"
        topology = build_topology(
            hardware,
            expected_num_devices=expected_num_devices,
        )
    else:
        kind = str(topology_config["kind"])
        if kind != "multi-dc-full-mesh":
            raise ValueError(f"unknown topology kind: {kind!r}")
        topology = build_multidc_topology(
            hardware,
            data_centers=int(topology_config["data_centers"]),
            servers_per_data_center=int(topology_config["servers_per_data_center"]),
            intra_dc_bandwidth_gbps=float(
                topology_config["intra_dc_bandwidth_gbps"]
            ),
            intra_dc_latency_ns=float(topology_config["intra_dc_latency_ns"]),
            inter_dc_bandwidth_gbps=float(
                topology_config["inter_dc_bandwidth_gbps"]
            ),
            inter_dc_latency_ns=float(topology_config["inter_dc_latency_ns"]),
            expected_num_devices=expected_num_devices,
        )
    target = Path(path)
    content = topology.serialize()
    target.write_text(content, encoding="utf-8")
    gateway_set = set(topology.gateway_ids)
    next_port: dict[int, int] = {}
    gateway_wan_ports: list[list[int]] = []
    for link in topology.links:
        src_port = next_port.get(link.src, 1)
        dst_port = next_port.get(link.dst, 1)
        next_port[link.src] = src_port + 1
        next_port[link.dst] = dst_port + 1
        if link.src in gateway_set and link.dst in gateway_set:
            gateway_wan_ports.extend(
                ([link.src, src_port], [link.dst, dst_port])
            )
    return {
        "topology_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "node_count": topology.node_count,
        "link_count": len(topology.links),
        "gpu_ids": list(topology.gpu_ids),
        "nvswitch_ids": list(topology.nvswitch_ids),
        "tor_id": topology.tor_id,
        "kind": kind,
        "switch_ids": list(topology.regular_switch_ids),
        "tor_ids": list(topology.tor_ids),
        "gateway_ids": list(topology.gateway_ids),
        "gateway_wan_ports": gateway_wan_ports,
        "tor_port_capacity_gbps": (
            float(topology_config["intra_dc_bandwidth_gbps"])
            if topology_config is not None
            else (
                hardware.link_bandwidth_gbps
                * hardware.gpus_per_server
                / hardware.oversubscription
            )
        ),
        "gateway_wan_port_capacity_gbps": (
            float(topology_config["inter_dc_bandwidth_gbps"])
            if topology_config is not None
            else None
        ),
    }
