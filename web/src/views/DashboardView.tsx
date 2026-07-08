import React, { useState, useEffect, useMemo } from 'react';
import {
  Layers, Activity, Search, ArrowUp, ArrowDown, ChevronRight, ChevronDown,
  Server, Network, Hash, Zap, AlertCircle, Shield, Globe,
  TrendingUp, Clock, AlertTriangle, XCircle, GitMerge, MapPin, Moon, HeartPulse
} from 'lucide-react';
import { formatBytes, formatIsoDate, formatSpeed, SubnetDef } from '../utils';
import NetworkBadge from '../components/NetworkBadge';

interface Summary {
  total_switches: number;
  online_switches: number;
  offline_switches: number;
  total_ports: number;
  up_ports: number;
  down_ports: number;
  total_vlans: number;
  total_macs: number;
  new_devices_24h: number;
  flapping_ports: number;
  unhealthy_ports: number;
}

interface VlanSummary {
  vlan_id: number;
  vlan_name: string;
  device_count: number;
  total_ports: number;
  devices: { device_id: number; device_hostname: string; untagged_count: number; tagged_count: number }[];
}

interface PortUtil {
  device_id: number;
  device_hostname: string;
  port_id: number;
  port_index: number;
  port_name: string;
  total_bytes: number;
  rx_bytes: number;
  tx_bytes: number;
  rx_errors: number;
  tx_errors: number;
}

interface NewDevice {
  mac_address: string;
  vendor?: string;
  ip_address?: string;
  hostname?: string;
  device_id: number;
  device_hostname: string;
  port_id?: number;
  port_name?: string;
  port_index?: number;
  vlan_id?: number;
  first_seen: string;
}

interface PortDiagnosis {
  code: string;
  severity: 'critical' | 'high' | 'medium' | 'info';
  summary: string;
}

interface PortHealthEntry {
  port_id: number;
  device_id: number;
  device_hostname: string;
  device_ip: string;
  port_name: string;
  port_index: number;
  port_description?: string;
  port_type?: string;
  oper_status: number;
  diagnoses: PortDiagnosis[];
  errors_24h: number;
  discards_24h: number;
  flap_count: number;
  last_flap_at?: string;
  last_error_at?: string;
  speed?: number;
  max_speed_seen?: number;
  duplex?: number;
  stp_state?: number;
  last_mac?: string;
  last_hostname?: string;
}

interface SwitchVitals {
  device_id: number;
  hostname: string;
  ip_address: string;
  poll_status: string;
  uptime_seconds?: number;
  cpu_util?: number;
  mem_used_pct?: number;
  temperature?: number;
  fans_ok?: boolean | null;
  psu_ok?: boolean | null;
  poe_budget_w?: number;
  poe_used_w?: number;
  stp_top_changes?: number;
  vitals_updated_at?: string;
}

interface SubnetUtil {
  subnet: string;
  total_hosts: number;
  used: number;
  pct_used: number;
  ips: { ip: string; mac: string; hostname?: string }[];
}

interface DarkPort {
  port_id: number; port_index: number; port_name: string; port_description?: string;
  port_type?: string; device_id: number; device_hostname: string;
  oper_status: number; last_connection_at?: string; total_bytes: number; flap_count: number;
}

interface DepartedDevice {
  mac_address: string; vendor?: string; ip_address?: string; hostname?: string;
  last_seen: string; days_gone: number;
}

interface ErrorPort {
  port_id: number; port_index: number; port_name: string; port_description?: string;
  port_type?: string; device_id: number; device_hostname: string; device_ip: string;
  oper_status: number; rx_errors: number; tx_errors: number; total_errors: number;
  total_bytes: number; err_rate_pct: number; speed?: number;
}

interface IpConflict {
  ip_address: string; mac_count: number;
  entries: { mac_address: string; hostname?: string; last_seen: string }[];
}

interface VlanGap {
  site: string;
  gaps: { vlan_id: number; vlan_name?: string; missing_from: { device_id: number; device_hostname: string }[]; present_on: number; total_switches: number }[];
}

interface SiteSummary {
  site: string; total_switches: number; online_switches: number;
  total_ports: number; up_ports: number; new_devices_24h: number; flapping_ports: number;
  devices: { id: number; hostname: string; ip_address: string; poll_status: string }[];
}

export default function DashboardView({ apiFetch, onJumpToFaceplate, onSearch, subnets = [], refreshKey }: {
  apiFetch: (url: string, options?: any) => Promise<Response>,
  onJumpToFaceplate: (deviceId: number, portIndex?: number) => void,
  onSearch: (query: string) => void,
  subnets?: SubnetDef[],
  refreshKey?: number,
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [vlans, setVlans] = useState<VlanSummary[]>([]);
  const [utils, setUtils] = useState<PortUtil[]>([]);
  const [newDevices, setNewDevices] = useState<NewDevice[]>([]);
  const [portHealth, setPortHealth] = useState<PortHealthEntry[]>([]);
  const [vitals, setVitals] = useState<SwitchVitals[]>([]);
  const [subnetUtils, setSubnetUtils] = useState<SubnetUtil[]>([]);
  const [darkPorts, setDarkPorts] = useState<DarkPort[]>([]);
  const [departedDevices, setDepartedDevices] = useState<DepartedDevice[]>([]);
  const [errorPorts, setErrorPorts] = useState<ErrorPort[]>([]);
  const [ipConflicts, setIpConflicts] = useState<IpConflict[]>([]);
  const [vlanGaps, setVlanGaps] = useState<VlanGap[]>([]);
  const [sites, setSites] = useState<SiteSummary[]>([]);
  const [expandedVlan, setExpandedVlan] = useState<number | null>(null);
  const [expandedSubnet, setExpandedSubnet] = useState<string | null>(null);
  const [vlanSearch, setVlanSearch] = useState('');
  const [utilSearch, setUtilSearch] = useState('');
  const [sortKey, setSortKey] = useState('total_bytes');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [isLoading, setIsLoading] = useState(true);
  const [activeSection, setActiveSection] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const [sumRes, vlansRes, utilRes, newDevRes, portHealthRes, vitalsRes, subnetRes,
             darkRes, departedRes, errRes, conflictsRes, gapsRes, sitesRes] = await Promise.all([
        apiFetch('/api/dashboard/summary'),
        apiFetch('/api/dashboard/vlans'),
        apiFetch('/api/dashboard/utilization'),
        apiFetch('/api/dashboard/new-devices'),
        apiFetch('/api/dashboard/port-health'),
        apiFetch('/api/dashboard/vitals'),
        apiFetch('/api/dashboard/subnet-utilization'),
        apiFetch('/api/dashboard/dark-ports'),
        apiFetch('/api/dashboard/departed-devices'),
        apiFetch('/api/dashboard/error-ports'),
        apiFetch('/api/dashboard/ip-conflicts'),
        apiFetch('/api/dashboard/vlan-gaps'),
        apiFetch('/api/dashboard/sites'),
      ]);
      const [sumData, vlansData, utilData, newDevData, portHealthData, vitalsData, subnetData,
              darkData, departedData, errData, conflictsData, gapsData, sitesData] = await Promise.all([
        sumRes.json(), vlansRes.json(), utilRes.json(),
        newDevRes.json(), portHealthRes.json(), vitalsRes.json(), subnetRes.json(),
        darkRes.json(), departedRes.json(), errRes.json(),
        conflictsRes.json(), gapsRes.json(), sitesRes.json(),
      ]);
      setSummary(sumData);
      setVlans(Array.isArray(vlansData) ? vlansData : []);
      setUtils(Array.isArray(utilData) ? utilData : []);
      setNewDevices(Array.isArray(newDevData) ? newDevData : []);
      setPortHealth(Array.isArray(portHealthData) ? portHealthData : []);
      setVitals(Array.isArray(vitalsData) ? vitalsData : []);
      setSubnetUtils(Array.isArray(subnetData) ? subnetData : []);
      setDarkPorts(Array.isArray(darkData) ? darkData : []);
      setDepartedDevices(Array.isArray(departedData) ? departedData : []);
      setErrorPorts(Array.isArray(errData) ? errData : []);
      setIpConflicts(Array.isArray(conflictsData) ? conflictsData : []);
      setVlanGaps(Array.isArray(gapsData) ? gapsData : []);
      setSites(Array.isArray(sitesData) ? sitesData : []);
    } catch (e) {
      console.error("Dashboard fetch failed", e);
    } finally {
      setIsLoading(false);
    }
  };

  const filteredVlans = useMemo(() => {
    if (!Array.isArray(vlans)) return [];
    return vlans.filter(v =>
      v.vlan_id.toString().includes(vlanSearch) ||
      (v.vlan_name || '').toLowerCase().includes(vlanSearch.toLowerCase())
    );
  }, [vlans, vlanSearch]);

  const filteredUtils = useMemo(() => {
    if (!Array.isArray(utils)) return [];
    return utils
      .filter(u => u.device_hostname.toLowerCase().includes(utilSearch.toLowerCase()) ||
        (u.port_name || '').toLowerCase().includes(utilSearch.toLowerCase()))
      .sort((a, b) => {
        const valA = (a as any)[sortKey] ?? 0;
        const valB = (b as any)[sortKey] ?? 0;
        return sortOrder === 'asc' ? valA - valB : valB - valA;
      });
  }, [utils, utilSearch, sortKey, sortOrder]);

  const visibleVitals = useMemo(() => vitals.filter(v => {
    if (v.poll_status === 'error') return true;
    return v.uptime_seconds != null || v.cpu_util != null || v.mem_used_pct != null ||
      v.temperature != null || v.fans_ok != null || v.psu_ok != null ||
      v.poe_budget_w != null || v.poe_used_w != null;
  }), [vitals]);

  const vitalsWarnCount = useMemo(() => vitals.filter(v =>
    (v.cpu_util ?? 0) >= 90 || (v.mem_used_pct ?? 0) >= 90 || (v.temperature ?? 0) >= 95 ||
    v.fans_ok === false || v.psu_ok === false
  ).length, [vitals]);

  function handleSort(k: string) {
    if (sortKey === k) setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortOrder('desc'); }
  }

  if (!isLoading && !summary) {
    return (
      <div className="p-12 text-center">
        <AlertCircle className="w-12 h-12 text-red/50 mx-auto mb-4" />
        <h2 className="text-xl font-bold text-white mb-2">No data available</h2>
        <p className="text-text2">Dashboard couldn't load. Ensure switches are being polled.</p>
        <button onClick={fetchData} className="mt-6 px-6 py-2 bg-accent text-white rounded-lg font-bold uppercase text-xs">Retry</button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6 h-full overflow-y-auto bg-bg text-text">

      {/* Sites Overview */}
      {sites.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionHeader icon={<MapPin className="w-4 h-4 text-accent2" />} title="Sites" count={sites.length} />
          <div className="flex gap-3 overflow-x-auto pb-1">
            {sites.map(site => (
              <div key={site.site} className="flex-shrink-0 bg-surface border border-border rounded-xl p-4 min-w-[200px]">
                <div className="text-sm font-bold text-white mb-2 truncate">{site.site || 'Default'}</div>
                <div className="space-y-1.5 text-[11px]">
                  <div className="flex justify-between">
                    <span className="text-text2">Switches</span>
                    <span className={site.online_switches === site.total_switches ? 'text-green font-bold' : 'text-yellow font-bold'}>
                      {site.online_switches}/{site.total_switches}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text2">Ports Up</span>
                    <span className="text-accent font-bold">{site.up_ports}/{site.total_ports}</span>
                  </div>
                  {site.flapping_ports > 0 && (
                    <div className="flex justify-between">
                      <span className="text-text2">Flapping</span>
                      <span className="text-yellow font-bold">{site.flapping_ports}</span>
                    </div>
                  )}
                  {site.new_devices_24h > 0 && (
                    <div className="flex justify-between">
                      <span className="text-text2">New (24h)</span>
                      <span className="text-red font-bold">{site.new_devices_24h}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Summary Cards */}
      <section className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
        <SummaryCard icon={<Server className="w-4 h-4" />} label="Switches" value={summary?.total_switches ?? 0} sub={`${summary?.online_switches ?? 0} online`} color="accent" />
        <SummaryCard icon={<Zap className="w-4 h-4" />} label="Active Ports" value={summary?.up_ports ?? 0} sub={`of ${summary?.total_ports ?? 0}`} color="green" />
        <SummaryCard icon={<Hash className="w-4 h-4" />} label="Active MACs" value={summary?.total_macs ?? 0} sub="last 2h" color="accent2" />
        <SummaryCard icon={<Network className="w-4 h-4" />} label="VLANs" value={summary?.total_vlans ?? 0} sub="across all" color="yellow" />
        <SummaryCard icon={<Shield className="w-4 h-4" />} label="New Devices" value={summary?.new_devices_24h ?? 0} sub="last 24h" color={summary?.new_devices_24h ? "red" : "green"} onClick={() => setActiveSection(activeSection === 'new' ? null : 'new')} active={activeSection === 'new'} />
        <SummaryCard icon={<AlertTriangle className="w-4 h-4" />} label="Port Health" value={summary?.unhealthy_ports ?? 0} sub="ports with issues" color={portHealth.some(p => p.diagnoses.some(d => d.severity === 'critical')) ? "red" : (summary?.unhealthy_ports ?? 0) > 0 ? "yellow" : "green"} onClick={() => setActiveSection(activeSection === 'porthealth' ? null : 'porthealth')} active={activeSection === 'porthealth'} />
        <SummaryCard icon={<HeartPulse className="w-4 h-4" />} label="Switch Vitals" value={vitalsWarnCount} sub="switches with warnings" color={vitalsWarnCount > 0 ? "red" : "green"} onClick={() => setActiveSection(activeSection === 'vitals' ? null : 'vitals')} active={activeSection === 'vitals'} />
        <SummaryCard icon={<XCircle className="w-4 h-4" />} label="Error Ports" value={errorPorts.length} sub="with errors" color={errorPorts.length > 0 ? "red" : "green"} onClick={() => setActiveSection(activeSection === 'errors' ? null : 'errors')} active={activeSection === 'errors'} />
        <SummaryCard icon={<GitMerge className="w-4 h-4" />} label="IP Conflicts" value={ipConflicts.length} sub="duplicate IPs" color={ipConflicts.length > 0 ? "red" : "green"} onClick={() => setActiveSection(activeSection === 'conflicts' ? null : 'conflicts')} active={activeSection === 'conflicts'} />
      </section>

      {/* New Devices Panel — shown when card clicked */}
      {activeSection === 'new' && (
        <section className="flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-200">
          <SectionHeader icon={<Shield className="w-4 h-4 text-red" />} title="New Devices (Last 24h)" count={newDevices.length} />
          {newDevices.length === 0 ? (
            <EmptyState icon={<Shield className="w-8 h-8" />} text="No new devices seen in the last 24 hours." />
          ) : (
            <div className="bg-surface border border-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left">MAC Address</th>
                    <th className="px-4 py-3 text-left">Vendor</th>
                    <th className="px-4 py-3 text-left">IP / Hostname</th>
                    <th className="px-4 py-3 text-left">Switch · Port</th>
                    <th className="px-4 py-3 text-left">First Seen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {newDevices.map((d) => (
                    <tr key={d.mac_address} className="hover:bg-accent/5 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-white">{d.mac_address}</td>
                      <td className="px-4 py-3 text-xs text-text2">{d.vendor || '—'}</td>
                      <td className="px-4 py-3 text-xs">
                        <div className="text-accent">{d.hostname || '—'}</div>
                        {d.ip_address && <div className="flex items-center text-text2">{d.ip_address}<NetworkBadge ip={d.ip_address} subnets={subnets} /></div>}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <button
                          onClick={() => d.port_index != null && onJumpToFaceplate(d.device_id, d.port_index)}
                          className="text-left hover:underline group"
                          disabled={d.port_index == null}
                        >
                          <span className="font-medium text-white group-hover:text-accent transition-colors">{d.device_hostname}</span>
                          <span className="text-text2"> · {d.port_name || '?'}</span>
                        </button>
                      </td>
                      <td className="px-4 py-3 text-xs text-text2">{formatIsoDate(d.first_seen, 'datetime')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Port Health Panel */}
      {activeSection === 'porthealth' && (
        <section className="flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-200">
          <SectionHeader icon={<AlertTriangle className="w-4 h-4 text-yellow" />} title="Port Health" count={portHealth.length} />
          {portHealth.length === 0 ? (
            <EmptyState icon={<Activity className="w-8 h-8" />} text="No port issues detected — all clean." />
          ) : (
            <div className="bg-surface border border-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left">Switch · Port</th>
                    <th className="px-4 py-3 text-left">Diagnosis</th>
                    <th className="px-4 py-3 text-left">Evidence</th>
                    <th className="px-4 py-3 text-left">Last Device</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {portHealth.map((p) => (
                    <tr key={p.port_id} className="hover:bg-accent/5 transition-colors align-top">
                      <td className="px-4 py-3 text-xs">
                        <button
                          onClick={() => onJumpToFaceplate(p.device_id, p.port_index)}
                          className="text-left hover:underline group"
                        >
                          <span className="font-medium text-white group-hover:text-accent transition-colors">{p.device_hostname}</span>
                          <span className="text-accent"> · {p.port_name}</span>
                        </button>
                        {p.port_description && <div className="text-text2 text-[10px]">{p.port_description}</div>}
                        <span className={`inline-block mt-1 px-2 py-0.5 text-[10px] font-bold rounded ${p.oper_status === 1 ? 'bg-green/20 text-green' : 'bg-red/20 text-red'}`}>
                          {p.oper_status === 1 ? 'UP' : 'DOWN'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1.5">
                          {p.diagnoses.map((d, i) => (
                            <div key={i} className="flex items-start gap-1.5">
                              <span className={`shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded uppercase ${SEVERITY_COLORS[d.severity] || SEVERITY_COLORS.info}`}>{d.severity}</span>
                              <span className="text-[11px] text-text2">{d.summary}</span>
                            </div>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-[11px] text-text2 space-y-0.5">
                        {p.errors_24h > 0 && <div>Errors (24h): <span className="text-red font-bold">{p.errors_24h}</span></div>}
                        {p.discards_24h > 0 && <div>Discards (24h): <span className="text-yellow font-bold">{p.discards_24h}</span></div>}
                        {p.max_speed_seen != null && p.speed != null && p.speed < p.max_speed_seen && (
                          <div className="text-orange-400">Downshifted: linked at {formatSpeed(p.speed)}, best seen {formatSpeed(p.max_speed_seen)}</div>
                        )}
                        {p.duplex === 2 && <div className="text-red">Half duplex</div>}
                        {p.last_flap_at && <div>Last flap: {relativeTime(p.last_flap_at)}</div>}
                        {p.last_error_at && <div>Last error: {relativeTime(p.last_error_at)}</div>}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <div className="font-mono text-text2">{p.last_mac || '—'}</div>
                        {p.last_hostname && <div className="text-accent">{p.last_hostname}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Switch Vitals Panel */}
      {activeSection === 'vitals' && (
        <section className="flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-200">
          <SectionHeader icon={<HeartPulse className="w-4 h-4 text-accent2" />} title="Switch Vitals" count={vitals.length} />
          {visibleVitals.length === 0 ? (
            <EmptyState icon={<HeartPulse className="w-8 h-8" />} text="No vitals data reported yet." />
          ) : (
            <div className="bg-surface border border-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left">Switch</th>
                    <th className="px-4 py-3 text-left">Uptime</th>
                    <th className="px-4 py-3 text-left">CPU</th>
                    <th className="px-4 py-3 text-left">Mem</th>
                    <th className="px-4 py-3 text-left">Temp</th>
                    <th className="px-4 py-3 text-left">Fan / PSU</th>
                    <th className="px-4 py-3 text-left">PoE</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {visibleVitals.map((v) => (
                    <tr key={v.device_id} className="hover:bg-accent/5 transition-colors">
                      <td className="px-4 py-3 text-xs">
                        <button onClick={() => onJumpToFaceplate(v.device_id)} className="text-left hover:underline group">
                          <span className="font-medium text-white group-hover:text-accent transition-colors">{v.hostname}</span>
                        </button>
                        {v.poll_status === 'error' && (
                          <div className="mt-1"><span className="px-2 py-0.5 bg-red/20 text-red text-[10px] font-bold rounded">ERROR</span></div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-text2">{humanizeUptime(v.uptime_seconds)}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`font-bold ${v.cpu_util == null ? 'text-text2' : v.cpu_util >= 90 ? 'text-red' : v.cpu_util >= 70 ? 'text-yellow' : 'text-text2'}`}>
                          {v.cpu_util != null ? `${v.cpu_util}%` : '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`font-bold ${v.mem_used_pct == null ? 'text-text2' : v.mem_used_pct >= 90 ? 'text-red' : v.mem_used_pct >= 70 ? 'text-yellow' : 'text-text2'}`}>
                          {v.mem_used_pct != null ? `${v.mem_used_pct}%` : '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`font-bold ${v.temperature == null ? 'text-text2' : v.temperature >= 95 ? 'text-red' : 'text-text2'}`}>
                          {v.temperature != null ? `${v.temperature}°C` : '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-1">
                          <VitalChip label="FAN" ok={v.fans_ok} />
                          <VitalChip label="PSU" ok={v.psu_ok} />
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {v.poe_budget_w != null && v.poe_used_w != null ? (
                          <div className="flex items-center gap-2">
                            <span className="text-text2 w-16">{v.poe_used_w.toFixed(0)} / {v.poe_budget_w.toFixed(0)} W</span>
                            <div className="flex-1 max-w-[70px] h-1.5 bg-surface2 rounded-full overflow-hidden">
                              <div className={`h-full rounded-full ${v.poe_used_w / v.poe_budget_w > 0.9 ? 'bg-red' : 'bg-accent2'}`} style={{ width: `${Math.min(100, (v.poe_used_w / v.poe_budget_w) * 100)}%` }} />
                            </div>
                          </div>
                        ) : <span className="text-text2">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Error Ports Panel */}
      {activeSection === 'errors' && (
        <section className="flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-200">
          <SectionHeader icon={<XCircle className="w-4 h-4 text-red" />} title="Error Ports" count={errorPorts.length} />
          {errorPorts.length === 0 ? (
            <EmptyState icon={<XCircle className="w-8 h-8" />} text="No ports with errors detected." />
          ) : (
            <div className="bg-surface border border-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left">Switch · Port</th>
                    <th className="px-4 py-3 text-left">Errors</th>
                    <th className="px-4 py-3 text-left">Error Rate</th>
                    <th className="px-4 py-3 text-left">Traffic</th>
                    <th className="px-4 py-3 text-left">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {errorPorts.map((p) => (
                    <tr key={p.port_id} className="hover:bg-accent/5 transition-colors">
                      <td className="px-4 py-3 text-xs">
                        <button onClick={() => onJumpToFaceplate(p.device_id, p.port_index)} className="text-left hover:underline group">
                          <span className="font-medium text-white group-hover:text-accent transition-colors">{p.device_hostname}</span>
                          <span className="text-accent"> · {p.port_name}</span>
                        </button>
                        {p.port_description && <div className="text-text2 text-[10px]">{p.port_description}</div>}
                      </td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 bg-red/20 text-red font-bold text-xs rounded">{p.total_errors.toLocaleString()}</span>
                        <div className="text-[10px] text-text2 mt-0.5">RX: {p.rx_errors} / TX: {p.tx_errors}</div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-bold ${p.err_rate_pct > 1 ? 'text-red' : 'text-yellow'}`}>{p.err_rate_pct.toFixed(2)}%</span>
                      </td>
                      <td className="px-4 py-3 text-xs text-text2">{formatBytes(p.total_bytes)}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${p.oper_status === 1 ? 'bg-green/20 text-green' : 'bg-red/20 text-red'}`}>
                          {p.oper_status === 1 ? 'UP' : 'DOWN'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* IP Conflicts Panel */}
      {activeSection === 'conflicts' && (
        <section className="flex flex-col gap-3 animate-in fade-in slide-in-from-top-2 duration-200">
          <SectionHeader icon={<GitMerge className="w-4 h-4 text-red" />} title="IP Conflicts" count={ipConflicts.length} />
          {ipConflicts.length === 0 ? (
            <EmptyState icon={<GitMerge className="w-8 h-8" />} text="No IP conflicts detected." />
          ) : (
            <div className="bg-surface border border-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left">IP Address</th>
                    <th className="px-4 py-3 text-left"># MACs</th>
                    <th className="px-4 py-3 text-left">Devices</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {ipConflicts.map((c) => (
                    <tr key={c.ip_address} className="hover:bg-accent/5 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-white">
                        <span className="flex items-center">{c.ip_address}<NetworkBadge ip={c.ip_address} subnets={subnets} /></span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 bg-red/20 text-red font-bold text-xs rounded">{c.mac_count}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1">
                          {c.entries.map((e) => (
                            <div key={e.mac_address} className="text-[11px]">
                              <span className="font-mono text-text2">{e.mac_address}</span>
                              {e.hostname && <span className="text-accent ml-2">{e.hostname}</span>}
                              <span className="text-text2/50 ml-2">{formatIsoDate(e.last_seen, 'date')}</span>
                            </div>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Port Traffic (Top 20, access ports only) */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <SectionHeader icon={<TrendingUp className="w-4 h-4 text-green" />} title="Port Traffic (Top 20)" count={filteredUtils.length} />
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text2" />
            <input className="bg-surface border border-border rounded-md pl-9 pr-3 py-1.5 text-xs focus:outline-none focus:border-accent w-56" placeholder="Filter by switch or port..." value={utilSearch} onChange={(e) => setUtilSearch(e.target.value)} />
          </div>
        </div>
        <div className="bg-surface border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
              <tr>
                <SortHeader label="Switch · Port" k="device_hostname" sortKey={sortKey} sortOrder={sortOrder} onClick={handleSort} />
                <SortHeader label="Total" k="total_bytes" sortKey={sortKey} sortOrder={sortOrder} onClick={handleSort} />
                <SortHeader label="RX" k="rx_bytes" sortKey={sortKey} sortOrder={sortOrder} onClick={handleSort} />
                <SortHeader label="TX" k="tx_bytes" sortKey={sortKey} sortOrder={sortOrder} onClick={handleSort} />
                <SortHeader label="Errors" k="rx_errors" sortKey={sortKey} sortOrder={sortOrder} onClick={handleSort} />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filteredUtils.map((u) => (
                <tr key={u.port_id} className="hover:bg-accent/5 transition-colors text-[13px]">
                  <td className="px-4 py-3">
                    <button onClick={() => onJumpToFaceplate(u.device_id, u.port_index)} className="text-left hover:underline group">
                      <span className="font-medium text-white group-hover:text-accent transition-colors">{u.device_hostname}</span>
                      <span className="text-accent font-mono text-xs"> · {u.port_name}</span>
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className="w-16 text-xs">{formatBytes(u.total_bytes)}</span>
                      <div className="flex-1 max-w-[80px] h-1.5 bg-surface2 rounded-full overflow-hidden">
                        <div className="h-full bg-accent rounded-full" style={{ width: `${Math.min(100, (u.total_bytes / (filteredUtils[0]?.total_bytes || 1)) * 100)}%` }} />
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-text2 text-xs">{formatBytes(u.rx_bytes)}</td>
                  <td className="px-4 py-3 text-text2 text-xs">{formatBytes(u.tx_bytes)}</td>
                  <td className={`px-4 py-3 font-bold text-xs ${u.rx_errors > 0 ? 'text-red' : 'text-text2 opacity-40'}`}>{u.rx_errors > 0 ? u.rx_errors : '—'}</td>
                </tr>
              ))}
              {filteredUtils.length === 0 && (
                <tr><td colSpan={5} className="px-6 py-12 text-center text-text2 italic">No traffic data</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* VLAN Explorer */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <SectionHeader icon={<Layers className="w-4 h-4 text-accent" />} title="VLAN Explorer" count={filteredVlans.length} />
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text2" />
            <input className="bg-surface border border-border rounded-md pl-9 pr-3 py-1.5 text-xs focus:outline-none focus:border-accent w-56" placeholder="Filter by ID or name..." value={vlanSearch} onChange={(e) => setVlanSearch(e.target.value)} />
          </div>
        </div>
        <div className="bg-surface border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
              <tr>
                <th className="px-4 py-3 w-8"></th>
                <th className="px-4 py-3 text-left">VLAN ID</th>
                <th className="px-4 py-3 text-left">Name</th>
                <th className="px-4 py-3 text-left">Switches</th>
                <th className="px-4 py-3 text-left">Total Ports</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filteredVlans.map((v) => (
                <React.Fragment key={v.vlan_id}>
                  <tr className="hover:bg-accent/5 cursor-pointer transition-colors" onClick={() => setExpandedVlan(expandedVlan === v.vlan_id ? null : v.vlan_id)}>
                    <td className="px-4 py-3">{expandedVlan === v.vlan_id ? <ChevronDown className="w-4 h-4 text-accent" /> : <ChevronRight className="w-4 h-4 text-text2" />}</td>
                    <td className="px-4 py-3 font-bold text-accent">{v.vlan_id}</td>
                    <td className="px-4 py-3 text-white">{v.vlan_name || '—'}</td>
                    <td className="px-4 py-3">{v.device_count}</td>
                    <td className="px-4 py-3">{v.total_ports}</td>
                  </tr>
                  {expandedVlan === v.vlan_id && (
                    <tr className="bg-bg/50">
                      <td colSpan={5} className="px-10 py-4">
                        <div className="flex flex-wrap gap-3">
                          {v.devices.map(d => (
                            <button key={d.device_id} onClick={() => onJumpToFaceplate(d.device_id)} className="bg-surface border border-border rounded-lg p-3 min-w-[160px] text-left hover:border-accent transition-colors">
                              <div className="text-xs font-bold text-white mb-1">{d.device_hostname}</div>
                              <div className="flex gap-3 text-[10px] text-text2 uppercase font-bold">
                                <span>{d.untagged_count} UNT</span>
                                <span>{d.tagged_count} TAG</span>
                              </div>
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {filteredVlans.length === 0 && (
                <tr><td colSpan={5} className="px-6 py-12 text-center text-text2 italic">No VLANs found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Subnet Utilization */}
      <section className="flex flex-col gap-3">
        <SectionHeader icon={<Globe className="w-4 h-4 text-accent2" />} title="IP Subnet Utilization" count={subnetUtils.length} />
        {subnetUtils.length === 0 ? (
          <EmptyState icon={<Globe className="w-8 h-8" />} text="No IP data available. Ensure at least one switch is collecting ARP." />
        ) : (
          <div className="bg-surface border border-border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                <tr>
                  <th className="px-4 py-3 text-left w-8"></th>
                  <th className="px-4 py-3 text-left">Subnet</th>
                  <th className="px-4 py-3 text-left">Used / Total</th>
                  <th className="px-4 py-3 text-left w-64">Utilization</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {subnetUtils.map((s) => (
                  <React.Fragment key={s.subnet}>
                    <tr className="hover:bg-accent/5 cursor-pointer transition-colors" onClick={() => setExpandedSubnet(expandedSubnet === s.subnet ? null : s.subnet)}>
                      <td className="px-4 py-3">{expandedSubnet === s.subnet ? <ChevronDown className="w-4 h-4 text-accent" /> : <ChevronRight className="w-4 h-4 text-text2" />}</td>
                      <td className="px-4 py-3 font-mono text-sm text-white">
                        <span className="flex items-center gap-2">
                          {s.subnet}
                          {(() => { const name = subnets.find(n => n.cidr === s.subnet && n.name)?.name; return name ? <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-accent2/15 text-accent2 uppercase tracking-wide font-sans">{name}</span> : null; })()}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className="font-bold text-white">{s.used}</span>
                        <span className="text-text2"> / {s.total_hosts}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <div className="flex-1 h-2 bg-surface2 rounded-full overflow-hidden">
                            <div className={`h-full rounded-full transition-all ${s.pct_used > 80 ? 'bg-red' : s.pct_used > 60 ? 'bg-yellow' : 'bg-accent'}`} style={{ width: `${s.pct_used}%` }} />
                          </div>
                          <span className="text-xs font-bold w-10 text-right text-text2">{s.pct_used}%</span>
                        </div>
                      </td>
                    </tr>
                    {expandedSubnet === s.subnet && (
                      <tr className="bg-bg/50">
                        <td colSpan={4} className="px-8 py-4">
                          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2 max-h-64 overflow-y-auto">
                            {s.ips.map(ip => (
                              <div key={ip.ip} className="bg-surface border border-border rounded p-2 text-[11px] space-y-0.5">
                                <span className="flex items-center flex-wrap gap-x-1"><button onClick={() => onSearch(ip.ip)} className="font-mono text-accent hover:underline">{ip.ip}</button><NetworkBadge ip={ip.ip} subnets={subnets} /></span>
                                {ip.hostname && <div className="text-text2 truncate">{ip.hostname}</div>}
                                <button onClick={() => onSearch(ip.mac)} className="font-mono text-text2/50 text-[9px] truncate hover:text-text2 block">{ip.mac}</button>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* VLAN Gaps — collapsed by default */}
      {vlanGaps.length > 0 && (
        <CollapsibleSection
          icon={<Layers className="w-4 h-4 text-yellow" />}
          title="VLAN Gaps"
          count={vlanGaps.reduce((a, s) => a + s.gaps.length, 0)}
          defaultCollapsed
        >
          <div className="flex flex-col gap-3">
            {vlanGaps.map(site => (
              <div key={site.site} className="bg-surface border border-border rounded-xl overflow-hidden">
                <div className="px-4 py-3 bg-surface2 border-b border-border">
                  <span className="text-xs font-bold text-white">{site.site || 'Default'}</span>
                  <span className="ml-2 text-[10px] text-text2">{site.gaps.length} gap{site.gaps.length !== 1 ? 's' : ''}</span>
                </div>
                <div className="divide-y divide-border">
                  {site.gaps.map((gap) => (
                    <div key={gap.vlan_id} className="px-4 py-3 flex items-start gap-4">
                      <div className="min-w-[80px]">
                        <span className="font-bold text-yellow text-sm">VLAN {gap.vlan_id}</span>
                        {gap.vlan_name && <div className="text-[10px] text-text2">{gap.vlan_name}</div>}
                      </div>
                      <div className="flex-1">
                        <div className="text-[10px] text-text2 mb-1">Present on {gap.present_on}/{gap.total_switches} switches · Missing from:</div>
                        <div className="flex flex-wrap gap-1">
                          {gap.missing_from.map(sw => (
                            <button key={sw.device_id} onClick={() => onJumpToFaceplate(sw.device_id)} className="px-2 py-0.5 bg-red/10 border border-red/20 text-red text-[10px] rounded font-medium hover:bg-red/20 transition-colors">{sw.device_hostname}</button>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      )}

      {/* Departed Devices — collapsed by default */}
      {departedDevices.length > 0 && (
        <CollapsibleSection
          icon={<Clock className="w-4 h-4 text-text2" />}
          title="Departed Devices"
          count={departedDevices.length}
          defaultCollapsed
        >
          <div className="bg-surface border border-border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                <tr>
                  <th className="px-4 py-3 text-left">MAC</th>
                  <th className="px-4 py-3 text-left">Vendor</th>
                  <th className="px-4 py-3 text-left">IP</th>
                  <th className="px-4 py-3 text-left">Hostname</th>
                  <th className="px-4 py-3 text-left">Last Seen</th>
                  <th className="px-4 py-3 text-left">Days Gone</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {departedDevices.map((d) => (
                  <tr key={d.mac_address} className="hover:bg-accent/5 transition-colors">
                    <td className="px-4 py-3 font-mono text-xs">
                      <button onClick={() => onSearch(d.mac_address)} className="text-text hover:text-accent hover:underline transition-colors">{d.mac_address}</button>
                    </td>
                    <td className="px-4 py-3 text-xs text-text2">{d.vendor || '—'}</td>
                    <td className="px-4 py-3 text-xs font-mono">
                      {d.ip_address
                        ? <span className="flex items-center"><button onClick={() => onSearch(d.ip_address!)} className="text-accent hover:underline">{d.ip_address}</button><NetworkBadge ip={d.ip_address} subnets={subnets} /></span>
                        : <span className="text-text2">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs text-white">{d.hostname || '—'}</td>
                    <td className="px-4 py-3 text-xs text-text2">{formatIsoDate(d.last_seen, 'date')}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${d.days_gone > 30 ? 'bg-red/20 text-red' : 'bg-yellow/20 text-yellow'}`}>{d.days_gone}d</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CollapsibleSection>
      )}

      {/* Dark Ports — collapsed by default */}
      {darkPorts.length > 0 && (
        <CollapsibleSection
          icon={<Moon className="w-4 h-4 text-text2" />}
          title="Dark Ports (No Recent Activity)"
          count={darkPorts.length}
          defaultCollapsed
        >
          <div className="bg-surface border border-border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-surface2 text-[11px] font-bold text-text2 uppercase tracking-wider">
                <tr>
                  <th className="px-4 py-3 text-left">Switch · Port</th>
                  <th className="px-4 py-3 text-left">Description</th>
                  <th className="px-4 py-3 text-left">Last Active</th>
                  <th className="px-4 py-3 text-left">Traffic</th>
                  <th className="px-4 py-3 text-left">Type</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {darkPorts.map((p) => (
                  <tr key={p.port_id} className="hover:bg-accent/5 transition-colors">
                    <td className="px-4 py-3 text-xs">
                      <button onClick={() => onJumpToFaceplate(p.device_id, p.port_index)} className="text-left hover:underline group">
                        <span className="font-medium text-white group-hover:text-accent transition-colors">{p.device_hostname}</span>
                        <span className="text-text2"> · {p.port_name}</span>
                      </button>
                    </td>
                    <td className="px-4 py-3 text-xs text-text2">{p.port_description || '—'}</td>
                    <td className="px-4 py-3 text-xs">
                      {p.last_connection_at
                        ? <span className="text-text2">{relativeTime(p.last_connection_at)}</span>
                        : <span className="text-red font-medium">Never</span>}
                    </td>
                    <td className="px-4 py-3 text-xs text-text2">{formatBytes(p.total_bytes)}</td>
                    <td className="px-4 py-3">
                      {p.port_type
                        ? <span className="px-2 py-0.5 bg-accent/10 text-accent text-[10px] font-bold rounded">{p.port_type}</span>
                        : <span className="text-text2/30 text-[10px]">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CollapsibleSection>
      )}
    </div>
  );
}

function CollapsibleSection({ icon, title, count, children, defaultCollapsed = false }: any) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  return (
    <section className="flex flex-col gap-3">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center gap-2 group text-left"
      >
        {icon}
        <h2 className="text-base font-bold group-hover:text-white transition-colors">{title}</h2>
        {count != null && <span className="text-[10px] bg-surface2 border border-border text-text2 px-2 py-0.5 rounded-full font-bold">{count}</span>}
        <span className="ml-auto text-text2 group-hover:text-white transition-colors">
          {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </span>
      </button>
      {!collapsed && children}
    </section>
  );
}

function SummaryCard({ icon, label, value, sub, color, onClick, active }: any) {
  const colorMap: Record<string, string> = {
    accent: 'text-accent border-accent/20',
    green:  'text-green border-green/20',
    yellow: 'text-yellow border-yellow/20',
    accent2:'text-accent2 border-accent2/20',
    red:    'text-red border-red/20',
  };
  const baseClasses = `bg-surface border p-4 rounded-xl shadow-sm transition-all ${colorMap[color] || colorMap.accent}`;
  const interactiveClasses = onClick ? 'cursor-pointer hover:scale-105 hover:shadow-lg' : '';
  const activeClasses = active ? 'ring-2 ring-current' : '';
  return (
    <div className={`${baseClasses} ${interactiveClasses} ${activeClasses}`} onClick={onClick}>
      <div className="flex items-center gap-2 mb-2 opacity-70">{icon}<span className="text-[10px] font-bold uppercase tracking-widest">{label}</span></div>
      <div className="text-2xl font-bold text-white">{value}</div>
      <div className="text-[10px] text-text2 mt-0.5">{sub}</div>
    </div>
  );
}

function SectionHeader({ icon, title, count }: any) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <h2 className="text-base font-bold">{title}</h2>
      {count != null && <span className="text-[10px] bg-surface2 border border-border text-text2 px-2 py-0.5 rounded-full font-bold">{count}</span>}
    </div>
  );
}

function EmptyState({ icon, text }: any) {
  return (
    <div className="h-32 flex flex-col items-center justify-center text-text2 border border-dashed border-border rounded-xl">
      <div className="opacity-20 mb-2">{icon}</div>
      <p className="text-sm">{text}</p>
    </div>
  );
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red/20 text-red',
  high: 'bg-orange-500/20 text-orange-400',
  medium: 'bg-yellow/20 text-yellow',
  info: 'bg-surface2 text-text2',
};

function humanizeUptime(seconds?: number | null): string {
  if (seconds == null) return '—';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const mins = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function VitalChip({ label, ok }: { label: string, ok?: boolean | null }) {
  const cls = ok == null ? 'bg-surface2 text-text2' : ok ? 'bg-green/20 text-green' : 'bg-red/20 text-red';
  const text = ok == null ? '—' : ok ? 'OK' : 'FAIL';
  return <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded ${cls}`}>{label} {text}</span>;
}

function relativeTime(isoStr: string): string {
  const diff = Date.now() - new Date(isoStr + 'Z').getTime();
  const days = Math.floor(diff / 86400000);
  if (days > 0) return `${days}d ago`;
  const hours = Math.floor(diff / 3600000);
  if (hours > 0) return `${hours}h ago`;
  const mins = Math.floor(diff / 60000);
  return mins > 0 ? `${mins}m ago` : 'just now';
}

function SortHeader({ label, k, sortKey, sortOrder, onClick }: any) {
  const active = sortKey === k;
  return (
    <th className="px-4 py-3 cursor-pointer hover:text-white transition-colors text-left" onClick={() => onClick(k)}>
      <div className="flex items-center gap-1.5">
        {label}
        <div className="flex flex-col opacity-30">
          <ArrowUp className={`w-2.5 h-2.5 ${active && sortOrder === 'asc' ? 'text-accent opacity-100' : ''}`} />
          <ArrowDown className={`w-2.5 h-2.5 ${active && sortOrder === 'desc' ? 'text-accent opacity-100' : ''}`} />
        </div>
      </div>
    </th>
  );
}
