"""Prometheus integration service — queries Prometheus HTTP API for network metrics."""

import requests
from typing import Optional


class PrometheusError(Exception):
    """Raised when a Prometheus query fails with a clear, actionable message."""
    pass


class PrometheusClient:
    def __init__(self, url: str, auth_type: str = "none",
                 username: str = "", password: str = "", bearer_token: str = ""):
        self.url = url.rstrip("/")
        self.auth_type = auth_type
        self.username = username
        self.password = password
        self.bearer_token = bearer_token

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.timeout = 15
        if self.auth_type == "basic" and self.username:
            s.auth = (self.username, self.password)
        elif self.auth_type == "bearer" and self.bearer_token:
            s.headers["Authorization"] = f"Bearer {self.bearer_token}"
        return s

    def test_connection(self) -> dict:
        """Test connectivity and return Prometheus build info."""
        try:
            r = self._session().get(f"{self.url}/api/v1/status/buildinfo")
        except requests.ConnectionError as e:
            raise PrometheusError(
                f"Cannot connect to Prometheus at {self.url}. "
                f"Check that the URL is correct and the server is running. "
                f"Detail: {e}"
            )
        except requests.Timeout:
            raise PrometheusError(
                f"Connection to Prometheus at {self.url} timed out after 15s. "
                f"The server may be overloaded or unreachable from this network."
            )
        if r.status_code == 401:
            raise PrometheusError(
                f"Authentication failed (HTTP 401). "
                f"Check your auth type, username/password, or bearer token."
            )
        if r.status_code == 403:
            raise PrometheusError(
                f"Access denied (HTTP 403). The credentials are valid but lack "
                f"permission to access the Prometheus API."
            )
        if r.status_code != 200:
            raise PrometheusError(
                f"Unexpected response from Prometheus (HTTP {r.status_code}). "
                f"Response: {r.text[:300]}"
            )
        data = r.json()
        if data.get("status") != "success":
            raise PrometheusError(f"Prometheus returned status={data.get('status')}: {data}")
        return data.get("data", {})

    def query(self, promql: str) -> list[dict]:
        """Execute an instant PromQL query. Returns list of result dicts."""
        try:
            r = self._session().get(f"{self.url}/api/v1/query", params={"query": promql})
        except requests.ConnectionError as e:
            raise PrometheusError(f"Lost connection to Prometheus at {self.url}: {e}")
        except requests.Timeout:
            raise PrometheusError(f"Query timed out: {promql[:100]}")

        if r.status_code != 200:
            raise PrometheusError(
                f"Query failed (HTTP {r.status_code}): {promql[:100]}. "
                f"Response: {r.text[:300]}"
            )
        data = r.json()
        if data.get("status") != "success":
            err = data.get("error", "unknown error")
            etype = data.get("errorType", "")
            raise PrometheusError(
                f"PromQL error ({etype}): {err}. Query: {promql[:200]}"
            )
        return data.get("data", {}).get("result", [])

    def query_range(self, promql: str, start: str, end: str, step: str) -> list[dict]:
        """Execute a range PromQL query."""
        try:
            r = self._session().get(f"{self.url}/api/v1/query_range", params={
                "query": promql, "start": start, "end": end, "step": step,
            })
        except requests.ConnectionError as e:
            raise PrometheusError(f"Lost connection to Prometheus at {self.url}: {e}")
        except requests.Timeout:
            raise PrometheusError(f"Range query timed out: {promql[:100]}")

        if r.status_code != 200:
            raise PrometheusError(
                f"Range query failed (HTTP {r.status_code}): {promql[:100]}. "
                f"Response: {r.text[:300]}"
            )
        data = r.json()
        if data.get("status") != "success":
            err = data.get("error", "unknown error")
            raise PrometheusError(f"PromQL error: {err}. Query: {promql[:200]}")
        return data.get("data", {}).get("result", [])

    def get_targets(self) -> list[dict]:
        """Get all scrape targets and their status."""
        try:
            r = self._session().get(f"{self.url}/api/v1/targets")
        except (requests.ConnectionError, requests.Timeout) as e:
            raise PrometheusError(f"Cannot fetch targets: {e}")
        if r.status_code != 200:
            raise PrometheusError(f"Failed to fetch targets (HTTP {r.status_code})")
        data = r.json()
        return data.get("data", {}).get("activeTargets", [])

    def discover_network_devices(self) -> list[dict]:
        """Discover devices by querying SNMP targets for sysName and interface data."""
        devices = []

        # Get all SNMP targets
        targets = self.get_targets()
        snmp_targets = [t for t in targets if t.get("labels", {}).get("job") == "snmp"]

        if not snmp_targets:
            raise PrometheusError(
                "No SNMP targets found in Prometheus. Make sure you have an 'snmp' job "
                "configured in prometheus.yml with your network devices as targets. "
                f"Found {len(targets)} total targets with jobs: "
                + ", ".join(sorted(set(t.get('labels', {}).get('job', '?') for t in targets)))
            )

        # Get sysName for all instances
        sys_names = {}
        results = self.query('sysName')
        for r in results:
            instance = r["metric"].get("instance", "")
            name = r["metric"].get("sysName", "")
            if instance and name:
                sys_names[instance] = name

        # Get interface info for each target
        for target in snmp_targets:
            instance = target.get("labels", {}).get("instance", "")
            health = target.get("health", "unknown")

            if health != "up":
                devices.append({
                    "instance": instance,
                    "hostname": sys_names.get(instance, ""),
                    "health": health,
                    "error": target.get("lastError", "Target not up"),
                    "interfaces": [],
                })
                continue

            # Get interfaces for this instance
            ifaces = []
            if_names = self.query(f'ifName{{instance="{instance}"}}')
            if_speeds = {}
            if_oper = {}
            if_descr = {}

            for r in self.query(f'ifHighSpeed{{instance="{instance}"}}'):
                idx = r["metric"].get("ifIndex", "")
                if idx:
                    if_speeds[idx] = float(r["value"][1])

            for r in self.query(f'ifOperStatus{{instance="{instance}"}}'):
                idx = r["metric"].get("ifIndex", "")
                if idx:
                    if_oper[idx] = int(float(r["value"][1]))

            for r in self.query(f'ifDescr{{instance="{instance}"}}'):
                idx = r["metric"].get("ifIndex", "")
                descr = r["metric"].get("ifDescr", "")
                if idx:
                    if_descr[idx] = descr

            for r in if_names:
                idx = r["metric"].get("ifIndex", "")
                name = r["metric"].get("ifName", "")
                speed_mbps = if_speeds.get(idx, 0)
                oper = if_oper.get(idx, 0)
                descr = if_descr.get(idx, name)

                # Skip loopback, null, management, and tunnel interfaces
                lower = name.lower()
                if any(skip in lower for skip in ["null", "loopback", "mgmt", "management", "srte_"]):
                    continue

                ifaces.append({
                    "ifIndex": idx,
                    "name": name,
                    "description": descr,
                    "speed_mbps": speed_mbps,
                    "speed_gbps": speed_mbps / 1000 if speed_mbps else 0,
                    "oper_status": "up" if oper == 1 else "down",
                })

            devices.append({
                "instance": instance,
                "hostname": sys_names.get(instance, instance),
                "health": health,
                "error": "",
                "interfaces": ifaces,
            })

        return devices

    def get_live_metrics(self, nodes: list[dict], links: list[dict],
                         config: dict) -> dict:
        """
        Get live link utilization from Prometheus.

        nodes: list of {id, name, prometheus_id, interfaces}
        links: list of {id, source_node_id, target_node_id, source_interface, target_interface, bandwidth_gbps}
        config: prometheus config fields (in_octets_metric, out_octets_metric, etc.)

        Returns: {link_utilizations: [...], errors: [...], timestamp: str}
        """
        import time

        rate_interval = config.get("rate_interval", "5m")
        in_metric = config.get("in_octets_metric", "ifHCInOctets")
        out_metric = config.get("out_octets_metric", "ifHCOutOctets")
        speed_metric = config.get("speed_metric", "ifHighSpeed")
        iface_label = config.get("interface_label", "ifName")
        instance_label = config.get("instance_label", "instance")

        # Build node lookup: node_id -> {prometheus_id, name}
        node_map = {n["id"]: n for n in nodes}

        errors = []
        link_utilizations = []

        # Batch query: get rates for ALL instances at once
        try:
            in_rates = self.query(f'rate({in_metric}[{rate_interval}]) * 8')
        except PrometheusError as e:
            errors.append(f"Failed to query inbound rates ({in_metric}): {e}")
            in_rates = []

        try:
            out_rates = self.query(f'rate({out_metric}[{rate_interval}]) * 8')
        except PrometheusError as e:
            errors.append(f"Failed to query outbound rates ({out_metric}): {e}")
            out_rates = []

        try:
            speeds = self.query(speed_metric)
        except PrometheusError as e:
            errors.append(f"Failed to query interface speeds ({speed_metric}): {e}")
            speeds = []

        # Build lookups: (instance, ifIndex) -> rate_bps
        def build_rate_map(results):
            m = {}
            for r in results:
                inst = r["metric"].get(instance_label, "")
                ifidx = r["metric"].get("ifIndex", "")
                val = float(r["value"][1]) if r["value"][1] != "NaN" else 0.0
                if inst and ifidx:
                    m[(inst, ifidx)] = val
            return m

        in_map = build_rate_map(in_rates)
        out_map = build_rate_map(out_rates)

        # Build speed lookup: (instance, ifIndex) -> speed_mbps
        speed_map = {}
        for r in speeds:
            inst = r["metric"].get(instance_label, "")
            ifidx = r["metric"].get("ifIndex", "")
            val = float(r["value"][1]) if r["value"][1] != "NaN" else 0
            if inst and ifidx:
                speed_map[(inst, ifidx)] = val

        # Build ifName -> ifIndex lookup per instance
        # We need this because links reference interfaces by name, not index
        iface_name_to_idx = {}  # (instance, ifName) -> ifIndex
        try:
            if_names = self.query(f'{iface_label}')
            for r in if_names:
                inst = r["metric"].get(instance_label, "")
                idx = r["metric"].get("ifIndex", "")
                name = r["metric"].get(iface_label, "")
                if inst and idx and name:
                    iface_name_to_idx[(inst, name)] = idx
        except PrometheusError as e:
            errors.append(f"Failed to query interface names: {e}")

        # Process each link
        for link in links:
            src_node = node_map.get(link["source_node_id"])
            tgt_node = node_map.get(link["target_node_id"])

            if not src_node or not tgt_node:
                continue

            src_prom_id = src_node.get("prometheus_id", "")
            tgt_prom_id = tgt_node.get("prometheus_id", "")

            if not src_prom_id and not tgt_prom_id:
                continue  # Neither node has prometheus mapping

            src_iface = link.get("source_interface", "")
            tgt_iface = link.get("target_interface", "")

            # Look up interface indices
            src_idx = iface_name_to_idx.get((src_prom_id, src_iface), "")
            tgt_idx = iface_name_to_idx.get((tgt_prom_id, tgt_iface), "")

            # Get rates for source side (outbound = traffic going over the link)
            src_out = out_map.get((src_prom_id, src_idx), 0) if src_idx else 0
            src_in = in_map.get((src_prom_id, src_idx), 0) if src_idx else 0

            # Get rates for target side
            tgt_out = out_map.get((tgt_prom_id, tgt_idx), 0) if tgt_idx else 0
            tgt_in = in_map.get((tgt_prom_id, tgt_idx), 0) if tgt_idx else 0

            # Bidirectional: max of (src_out, tgt_in) for A->B, max of (tgt_out, src_in) for B->A
            # Overall link utilization = max of both directions
            a_to_b_bps = max(src_out, tgt_in)
            b_to_a_bps = max(tgt_out, src_in)
            max_bps = max(a_to_b_bps, b_to_a_bps)

            used_gbps = max_bps / 1e9
            bandwidth = link.get("bandwidth_gbps", 0)

            # Determine speed from Prometheus or fall back to link bandwidth
            src_speed_mbps = speed_map.get((src_prom_id, src_idx), 0) if src_idx else 0
            tgt_speed_mbps = speed_map.get((tgt_prom_id, tgt_idx), 0) if tgt_idx else 0
            prom_speed_gbps = max(src_speed_mbps, tgt_speed_mbps) / 1000 if max(src_speed_mbps, tgt_speed_mbps) > 0 else 0

            effective_bw = prom_speed_gbps if prom_speed_gbps > 0 else bandwidth
            utilization_pct = (used_gbps / effective_bw * 100) if effective_bw > 0 else 0

            link_utilizations.append({
                "link_id": link["id"],
                "source_node_id": link["source_node_id"],
                "target_node_id": link["target_node_id"],
                "bandwidth_gbps": effective_bw,
                "used_gbps": round(used_gbps, 4),
                "utilization_pct": round(utilization_pct, 1),
                "a_to_b_gbps": round(a_to_b_bps / 1e9, 4),
                "b_to_a_gbps": round(b_to_a_bps / 1e9, 4),
                "flow_ids": [],
                "cost_per_gb": 0,
                "has_data": bool(src_idx or tgt_idx),
            })

        return {
            "link_utilizations": link_utilizations,
            "errors": errors,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def get_historical_metrics(self, nodes: list[dict], links: list[dict],
                                config: dict, days: int = 10,
                                mode: str = "avg") -> dict:
        """
        Get historical average or max utilization over the last N days.
        mode: "avg" or "max"
        """
        import time

        rate_interval = config.get("rate_interval", "5m")
        in_metric = config.get("in_octets_metric", "ifHCInOctets")
        out_metric = config.get("out_octets_metric", "ifHCOutOctets")
        iface_label = config.get("interface_label", "ifName")
        instance_label = config.get("instance_label", "instance")
        speed_metric = config.get("speed_metric", "ifHighSpeed")

        agg_fn = "avg_over_time" if mode == "avg" else "max_over_time"
        range_str = f"{days}d"

        errors = []
        node_map = {n["id"]: n for n in nodes}

        # Query aggregated rates over the period
        try:
            in_results = self.query(
                f'{agg_fn}(rate({in_metric}[{rate_interval}])[{range_str}:]) * 8'
            )
        except PrometheusError as e:
            errors.append(f"Historical inbound query failed: {e}")
            in_results = []

        try:
            out_results = self.query(
                f'{agg_fn}(rate({out_metric}[{rate_interval}])[{range_str}:]) * 8'
            )
        except PrometheusError as e:
            errors.append(f"Historical outbound query failed: {e}")
            out_results = []

        try:
            speed_results = self.query(speed_metric)
        except PrometheusError as e:
            errors.append(f"Speed query failed: {e}")
            speed_results = []

        # Build lookups
        def build_map(results):
            m = {}
            for r in results:
                inst = r["metric"].get(instance_label, "")
                idx = r["metric"].get("ifIndex", "")
                val = float(r["value"][1]) if r["value"][1] != "NaN" else 0
                if inst and idx:
                    m[(inst, idx)] = val
            return m

        in_map = build_map(in_results)
        out_map = build_map(out_results)
        speed_map = {}
        for r in speed_results:
            inst = r["metric"].get(instance_label, "")
            idx = r["metric"].get("ifIndex", "")
            val = float(r["value"][1]) if r["value"][1] != "NaN" else 0
            if inst and idx:
                speed_map[(inst, idx)] = val

        # ifName -> ifIndex
        iface_name_to_idx = {}
        try:
            for r in self.query(iface_label):
                inst = r["metric"].get(instance_label, "")
                idx = r["metric"].get("ifIndex", "")
                name = r["metric"].get(iface_label, "")
                if inst and idx and name:
                    iface_name_to_idx[(inst, name)] = idx
        except PrometheusError:
            pass

        link_utilizations = []
        for link in links:
            src_node = node_map.get(link["source_node_id"])
            tgt_node = node_map.get(link["target_node_id"])
            if not src_node or not tgt_node:
                continue

            src_prom = src_node.get("prometheus_id", "")
            tgt_prom = tgt_node.get("prometheus_id", "")
            if not src_prom and not tgt_prom:
                continue

            src_idx = iface_name_to_idx.get((src_prom, link.get("source_interface", "")), "")
            tgt_idx = iface_name_to_idx.get((tgt_prom, link.get("target_interface", "")), "")

            src_out = out_map.get((src_prom, src_idx), 0) if src_idx else 0
            src_in = in_map.get((src_prom, src_idx), 0) if src_idx else 0
            tgt_out = out_map.get((tgt_prom, tgt_idx), 0) if tgt_idx else 0
            tgt_in = in_map.get((tgt_prom, tgt_idx), 0) if tgt_idx else 0

            max_bps = max(max(src_out, tgt_in), max(tgt_out, src_in))
            used_gbps = max_bps / 1e9
            bandwidth = link.get("bandwidth_gbps", 0)

            src_speed = speed_map.get((src_prom, src_idx), 0) if src_idx else 0
            tgt_speed = speed_map.get((tgt_prom, tgt_idx), 0) if tgt_idx else 0
            prom_speed = max(src_speed, tgt_speed) / 1000 if max(src_speed, tgt_speed) > 0 else 0
            effective_bw = prom_speed if prom_speed > 0 else bandwidth
            pct = (used_gbps / effective_bw * 100) if effective_bw > 0 else 0

            link_utilizations.append({
                "link_id": link["id"],
                "source_node_id": link["source_node_id"],
                "target_node_id": link["target_node_id"],
                "bandwidth_gbps": effective_bw,
                "used_gbps": round(used_gbps, 4),
                "utilization_pct": round(pct, 1),
                "flow_ids": [],
                "cost_per_gb": 0,
                "has_data": bool(src_idx or tgt_idx),
            })

        return {
            "link_utilizations": link_utilizations,
            "errors": errors,
            "mode": mode,
            "days": days,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
