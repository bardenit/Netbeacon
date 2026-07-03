import { useState, useEffect, useMemo } from 'react';
import { RefreshCw, Info, Cpu, Activity, Tag } from 'lucide-react';
import { formatSpeed, formatBytes, formatIsoDate, SubnetDef } from '../utils';
import NetworkBadge from '../components/NetworkBadge';
import { LabelEntry } from '../App';

interface PortVlan {
  vlan_id: number;
  vlan_name: string;
  tagged: boolean;
}

interface Port {
  id: number;
  port_index: number;
  port_name: string;
  port_description?: string;
  oper_status: number;
  admin_status: number;
  speed: number | null;
  rx_bytes: number | null;
  tx_bytes: number | null;
  rx_errors: number | null;
  tx_errors: number | null;
  vlans: PortVlan[];
  lldp_neighbor?: string;
  lldp_neighbor_chassis_id?: string;
  lldp_neighbor_vendor?: string;
  last_mac?: string;
  last_hostname?: string;
  last_ip?: string;
  last_connection_at?: string;
  flap_count?: number;
  last_flap_at?: string;
  notes?: string;
  poe_draw_mw?: number;
  port_type?: string;
}

interface PortHistoryEntry {
  timestamp: string;
  rx_bytes: number;
  tx_bytes: number;
}

interface MacHistoryEntry {
  mac_address: string;
  ip_address?: string;
  hostname?: string;
  vendor?: string;
  first_seen: string;
  last_seen: string;
}

interface MacEntry {
  mac_address: string;
  ip_address?: string;
  hostname?: string;
  vendor?: string;
  vlan_id?: number;
  port_index: number;
}

interface Device {
  id: number;
  hostname: string;
  snmp_name?: string;
  ip_address: string;
  vendor?: string;
  model?: string;
  firmware_version?: string;
  last_polled?: string;
  poll_status: string;
}

export default function FaceplateView({ initialParams, onParamsConsumed, apiFetch, subnets = [], labels = new Map(), onLabelMac, refreshKey, onNavChange }: {
  initialParams: { deviceId: number, portIndex?: number } | null,
  onParamsConsumed: () => void,
  apiFetch: (url: string, options?: any) => Promise<Response>,
  subnets?: SubnetDef[],
  labels?: Map<string, LabelEntry>,
  onLabelMac?: (mac: string) => void,
  refreshKey?: number,
  onNavChange?: (deviceId: number | null, portIndex: number | null) => void,
}) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [ports, setPorts] = useState<Port[]>([]);
  const [macEntries, setMacEntries] = useState<Record<number, MacEntry[]>>({});
  const [selectedPortIndex, setSelectedPortIndex] = useState<number | null>(null);
  const [activeVlanFilter, setActiveVlanFilter] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [tooltip, setTooltip] = useState<{ x: number, y: number, port: Port } | null>(null);
  const [macHistory, setMacHistory] = useState<MacHistoryEntry[]>([]);
  const [portHistory, setPortHistory] = useState<PortHistoryEntry[]>([]);

  useEffect(() => {
    apiFetch('/api/devices').then(res => res.json()).then(setDevices);
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fetch port data whenever selected device changes or Refresh is clicked
  useEffect(() => {
    if (selectedDeviceId) loadDeviceData(selectedDeviceId);
  }, [selectedDeviceId, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle jump from Search or hash restore on load
  useEffect(() => {
    if (initialParams) {
      const { deviceId, portIndex } = initialParams;
      setSelectedDeviceId(deviceId);
      if (portIndex !== undefined) setSelectedPortIndex(portIndex);
      onNavChange?.(deviceId, portIndex ?? null);
      onParamsConsumed();
    }
  }, [initialParams]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch port history and MAC history when selected port changes
  useEffect(() => {
    setMacHistory([]);
    setPortHistory([]);
    if (selectedPortIndex == null || !selectedDeviceId) return;
    const port = ports.find(p => p.port_index === selectedPortIndex);
    if (!port) return;
    apiFetch(`/api/devices/${selectedDeviceId}/ports/${port.id}/mac-history`)
      .then(res => res.json())
      .then((data: MacHistoryEntry[]) => {
        const sorted = [...data].sort((a, b) => new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime());
        setMacHistory(sorted.slice(0, 10));
      })
      .catch(() => {});
    apiFetch(`/api/dashboard/port-history/${port.id}?hours=24`)
      .then(res => res.json())
      .then((data: PortHistoryEntry[]) => setPortHistory(data))
      .catch(() => {});
  }, [selectedPortIndex, selectedDeviceId]);

  const handleDeviceChange = (id: string) => {
    const deviceId = id ? parseInt(id) : null;
    setSelectedDeviceId(deviceId);
    setSelectedPortIndex(null);
    setActiveVlanFilter(null);
    onNavChange?.(deviceId, null);
  };

  const handlePortTypeChange = async (portType: string | null) => {
    if (!selectedDeviceId || !selectedPort) return;
    try {
      await apiFetch(`/api/devices/${selectedDeviceId}/ports/${selectedPort.id}/type`, {
        method: 'PATCH',
        body: JSON.stringify({ port_type: portType }),
      });
      // Update local port state
      setPorts(prev => prev.map(p => p.id === selectedPort.id ? { ...p, port_type: portType ?? undefined } : p));
    } catch (e) {
      console.error('Failed to update port type', e);
    }
  };

  const loadDeviceData = async (id: number) => {
    setIsLoading(true);
    try {
      const [portsRes, fdbRes] = await Promise.all([
        apiFetch(`/api/devices/${id}/ports`),
        apiFetch(`/api/devices/${id}/fdb`)
      ]);
      const portsData = await portsRes.json();
      const fdbData = await fdbRes.json();
      
      setPorts(portsData);
      
      const macMap: Record<number, MacEntry[]> = {};
      fdbData.forEach((entry: MacEntry) => {
        if (entry.port_index == null) return;
        if (!macMap[entry.port_index]) macMap[entry.port_index] = [];
        macMap[entry.port_index].push(entry);
      });
      setMacEntries(macMap);
    } catch (e) {
      console.error("Failed to load device data", e);
    } finally {
      setIsLoading(false);
    }
  };

  const selectedDevice = devices.find(d => d.id === selectedDeviceId);
  const selectedPort = ports.find(p => p.port_index === selectedPortIndex);

  // Group physical ports by slot
  const physicalPortsBySlot = useMemo(() => {
    const slotPortRe = /^\d+:\d+$/;
    const excludeRe = /^(vlan|mgmt|loopback|lo|tunnel|null|cpu|stack|monitor)/i;

    const physical = ports.filter(p => {
      const name = p.port_name || '';
      if (excludeRe.test(name)) return false;
      if (slotPortRe.test(name)) return true;
      return name && p.port_index < 100000;
    }).sort((a, b) => {
      const aMatch = (a.port_name || '').match(/^(\d+):(\d+)$/);
      const bMatch = (b.port_name || '').match(/^(\d+):(\d+)$/);
      if (aMatch && bMatch) {
        const slotDiff = parseInt(aMatch[1]) - parseInt(bMatch[1]);
        return slotDiff !== 0 ? slotDiff : parseInt(aMatch[2]) - parseInt(bMatch[2]);
      }
      return a.port_index - b.port_index;
    });

    const slots: Record<string, Port[]> = {};
    physical.forEach(p => {
      const m = (p.port_name || '').match(/^(\d+):/);
      const slot = m ? m[1] : '1';
      if (!slots[slot]) slots[slot] = [];
      slots[slot].push(p);
    });
    return slots;
  }, [ports]);

  const uniqueVlans = useMemo(() => {
    const vMap: Record<number, string> = {};
    ports.forEach(p => p.vlans.forEach(v => { vMap[v.vlan_id] = v.vlan_name; }));
    return Object.entries(vMap).map(([id, name]) => ({ id: parseInt(id), name })).sort((a, b) => a.id - b.id);
  }, [ports]);

  const selectPort = (index: number | null) => {
    setSelectedPortIndex(index);
    onNavChange?.(selectedDeviceId, index);
  };

  return (
    <div className="flex flex-col gap-6 p-6 h-full overflow-y-auto">
      {/* Selector */}
      <div className="flex items-center gap-3">
        <select 
          className="bg-surface text-text border border-border px-3 py-2 rounded-md text-sm min-w-[280px] focus:outline-none focus:border-accent"
          value={selectedDeviceId || ''}
          onChange={(e) => handleDeviceChange(e.target.value)}
        >
          <option value="">— Select a switch —</option>
          {devices.map(d => (
            <option key={d.id} value={d.id}>{d.snmp_name || d.hostname} ({d.ip_address})</option>
          ))}
        </select>
        <button 
          onClick={() => selectedDeviceId && loadDeviceData(selectedDeviceId)}
          disabled={!selectedDeviceId || isLoading}
          className="p-2 bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {selectedDevice && (
        <>
          {/* Info Header */}
          <div className="bg-surface border border-border rounded-lg p-4 shadow-sm">
            <h2 className="text-lg font-bold mb-3">{selectedDevice.snmp_name || selectedDevice.hostname}</h2>
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-text2">
              <div className="flex items-center gap-1.5"><Activity className="w-3.5 h-3.5" /> IP: <span className="flex items-center text-text font-medium">{selectedDevice.ip_address}<NetworkBadge ip={selectedDevice.ip_address} subnets={subnets} /></span></div>
              <div className="flex items-center gap-1.5"><Cpu className="w-3.5 h-3.5" /> Vendor: <span className="text-text font-medium">{selectedDevice.vendor || '—'}</span></div>
              <div className="flex items-center gap-1.5"><Info className="w-3.5 h-3.5" /> Model: <span className="text-text font-medium">{selectedDevice.model || '—'}</span></div>
              <div className="flex items-center gap-1.5">Last Polled: <span className="text-text font-medium">{selectedDevice.last_polled ? formatIsoDate(selectedDevice.last_polled, 'datetime') : 'Never'}</span></div>
              <div className="flex items-center gap-1.5">Status: <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                selectedDevice.poll_status === 'ok' ? 'bg-green/20 text-green' : 'bg-red/20 text-red'
              }`}>{selectedDevice.poll_status}</span></div>
            </div>
          </div>

          {/* Faceplate Grid Area */}
          <div className="bg-surface border border-border rounded-lg p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
              <div className="flex gap-4 text-[11px] text-text2">
                <LegendItem color="bg-green/80" label="Up + MACs" />
                <LegendItem color="bg-surface2" label="Up, empty" />
                <LegendItem color="bg-bg" label="Down" />
                <LegendItem color="bg-orange-500/60" label="Wireless AP" />
                <LegendItem color="bg-accent2/80" label="Trunk / Uplink" />
              </div>

              {uniqueVlans.length > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-text2">Filter VLAN:</span>
                  <div className="flex flex-wrap gap-1">
                    <button 
                      onClick={() => setActiveVlanFilter(null)}
                      className={`px-2 py-1 rounded-full text-[10px] font-medium border transition-all ${
                        activeVlanFilter === null ? 'bg-accent border-accent text-white' : 'bg-surface2 border-border text-text2 hover:border-accent'
                      }`}
                    >All</button>
                    {uniqueVlans.map(v => (
                      <button 
                        key={v.id}
                        onClick={() => setActiveVlanFilter(v.id)}
                        className={`px-2 py-1 rounded-full text-[10px] font-medium border transition-all ${
                          activeVlanFilter === v.id ? 'bg-accent border-accent text-white' : 'bg-surface2 border-border text-text2 hover:border-accent'
                        }`}
                      >{v.id}</button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex flex-col gap-4">
              {Object.entries(physicalPortsBySlot).sort().map(([slot, slotPorts]) => (
                <div key={slot} className="flex flex-col gap-2">
                  {Object.keys(physicalPortsBySlot).length > 1 && (
                    <div className="text-[11px] text-text2 font-medium">Slot {slot}</div>
                  )}
                  <div 
                    className="grid gap-1" 
                    style={{ 
                      gridTemplateColumns: `repeat(${Math.ceil(slotPorts.length / 2)}, 38px)`,
                      gridTemplateRows: '28px 28px'
                    }}
                  >
                    {/* Render top row then bottom row for typical switch layout */}
                    {Array.from({ length: Math.ceil(slotPorts.length / 2) }).map((_, col) => {
                      const top = slotPorts[col * 2];
                      const bot = slotPorts[col * 2 + 1];
                      return (
                        <div key={col} className="contents">
                          {top && <PortBlock
                            port={top}
                            macs={macEntries[top.port_index] || []}
                            activeVlan={activeVlanFilter}
                            isSelected={selectedPortIndex === top.port_index}
                            onClick={() => selectPort(top.port_index)}
                            onHover={(e: any, p: any) => setTooltip(p ? { x: e.clientX, y: e.clientY, port: p } : null)}
                          />}
                          {bot && <PortBlock
                            port={bot}
                            macs={macEntries[bot.port_index] || []}
                            activeVlan={activeVlanFilter}
                            isSelected={selectedPortIndex === bot.port_index}
                            onClick={() => selectPort(bot.port_index)}
                            onHover={(e: any, p: any) => setTooltip(p ? { x: e.clientX, y: e.clientY, port: p } : null)}
                          />}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Detail Panel */}
          {selectedPort && (
            <div className="bg-surface border border-border rounded-lg p-6 shadow-sm animate-in fade-in slide-in-from-bottom-2 duration-200">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold">{selectedPort.port_name} {selectedPort.port_description && `— ${selectedPort.port_description}`}</h3>
                <button onClick={() => selectPort(null)} className="text-text2 hover:text-white">✕</button>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
                <div>
                  <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">Status & Performance</h4>
                  <div className="space-y-2">
                    <DetailRow label="Oper Status" value={selectedPort.oper_status === 1 ? 'Up' : 'Down'} color={selectedPort.oper_status === 1 ? 'text-green' : 'text-red'} />
                    <DetailRow label="Speed" value={formatSpeed(selectedPort.speed)} />
                    <DetailRow label="RX Bytes" value={formatBytes(selectedPort.rx_bytes)} />
                    <DetailRow label="TX Bytes" value={formatBytes(selectedPort.tx_bytes)} />
                    {selectedPort.rx_errors != null && <DetailRow label="RX Errors" value={selectedPort.rx_errors.toString()} color={selectedPort.rx_errors > 0 ? 'text-red' : ''} />}
                  </div>
                </div>

                <div>
                  <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">VLAN Configuration</h4>
                  <div className="flex flex-wrap gap-2">
                    {selectedPort.vlans.map(v => (
                      <span key={v.vlan_id} className={`px-2 py-1 rounded border text-[10px] font-medium ${
                        v.tagged ? 'bg-surface2 border-border text-text2' : 'bg-blue/10 border-blue/30 text-blue'
                      }`}>
                        VLAN {v.vlan_id} {v.vlan_name && `(${v.vlan_name})`} {v.tagged ? 'TAG' : 'UNT'}
                      </span>
                    ))}
                  </div>
                </div>

                <div>
                  <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">Connected (MACs)</h4>
                  <div className="space-y-1.5 max-h-[200px] overflow-y-auto pr-2">
                    {(macEntries[selectedPort.port_index] || []).map(m => {
                      const lbl = labels.get(m.mac_address);
                      return (
                        <div key={m.mac_address} className="flex flex-col p-2 bg-surface2 rounded border border-border/50 text-[11px]">
                          <div className="flex items-center justify-between gap-1">
                            <span className="font-mono text-text">{m.mac_address}</span>
                            <button
                              onClick={() => onLabelMac?.(m.mac_address)}
                              title={lbl ? `Edit label: ${lbl.label}` : 'Add MAC name'}
                              className="text-text2 hover:text-accent transition-colors flex-shrink-0"
                            >
                              <Tag className="w-3 h-3" />
                            </button>
                          </div>
                          {lbl && <div className="text-accent font-medium mt-0.5">{lbl.label}{lbl.vendor ? ` · ${lbl.vendor}` : ''}</div>}
                          {m.ip_address && <div className="flex items-center text-accent/80">{m.ip_address}<NetworkBadge ip={m.ip_address} subnets={subnets} /></div>}
                          {m.hostname && <div className="text-text2 italic">{m.hostname}</div>}
                        </div>
                      );
                    })}
                    {(!macEntries[selectedPort.port_index]?.length) && <div className="text-text2 italic text-[11px]">No MACs detected</div>}
                  </div>
                </div>

                <div>
                  <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">Port Intelligence</h4>
                  <div className="space-y-2">
                    {selectedPort.flap_count != null && selectedPort.flap_count > 0 && (
                      <div className="flex items-center gap-2 px-2 py-1.5 bg-yellow/10 border border-yellow/20 rounded text-[11px]">
                        <span className="text-yellow font-bold">⚡ {selectedPort.flap_count} link flap{selectedPort.flap_count !== 1 ? 's' : ''}</span>
                        {selectedPort.last_flap_at && <span className="text-text2">· {formatIsoDate(selectedPort.last_flap_at, 'datetime')}</span>}
                      </div>
                    )}
                    {selectedPort.poe_draw_mw != null && selectedPort.poe_draw_mw > 0 && (
                      <DetailRow label="PoE Draw" value={`${(selectedPort.poe_draw_mw / 1000).toFixed(1)} W`} color="text-accent2" />
                    )}
                    {selectedPort.oper_status !== 1 && selectedPort.last_mac && (
                      <div className="p-2 bg-surface2 rounded border border-border/50 space-y-1">
                        <div className="text-[10px] font-bold text-text2 uppercase tracking-wider">Last Device</div>
                        <div className="font-mono text-[11px] text-text">{selectedPort.last_mac}</div>
                        {selectedPort.last_hostname && <div className="text-[11px] text-accent italic">{selectedPort.last_hostname}</div>}
                        {selectedPort.last_ip && <div className="flex items-center text-[11px] text-text2">{selectedPort.last_ip}<NetworkBadge ip={selectedPort.last_ip} subnets={subnets} /></div>}
                        {selectedPort.last_connection_at && <div className="text-[10px] text-text2/60">Last seen: {formatIsoDate(selectedPort.last_connection_at, 'date')}</div>}
                      </div>
                    )}
                    <div className="space-y-1">
                      <div className="text-[10px] font-bold text-text2 uppercase tracking-wider">Notes</div>
                      <PortNotesEditor
                        key={selectedPort.id}
                        portId={selectedPort.id}
                        deviceId={selectedDeviceId!}
                        initialNotes={selectedPort.notes || ''}
                        apiFetch={apiFetch}
                        onSave={(notes) => setPorts(prev => prev.map(p => p.id === selectedPort.id ? { ...p, notes } : p))}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Port Type Selector */}
              <div className="mt-6 pt-5 border-t border-border">
                <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">Port Type</h4>
                <div className="flex flex-wrap gap-2">
                  {['AP', 'Phone', 'Server', 'Printer', 'Workstation', 'Uplink', 'Trunk', 'Unused'].map(type => {
                    const isActive = selectedPort.port_type === type;
                    return (
                      <button
                        key={type}
                        onClick={() => handlePortTypeChange(isActive ? null : type)}
                        className={`px-3 py-1 rounded-full text-[11px] font-medium border transition-all ${
                          isActive
                            ? 'bg-accent border-accent text-white'
                            : 'bg-surface2 border-border text-text2 hover:border-accent hover:text-text'
                        }`}
                      >
                        {type}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Traffic History Sparkline */}
              <div className="mt-5 pt-5 border-t border-border">
                <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">Traffic History (24h)</h4>
                {portHistory.length < 2 ? (
                  <div className="h-[60px] flex items-center justify-center text-text2 text-xs">Not enough data</div>
                ) : (
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center gap-3">
                      <span className="text-[10px] text-text2 w-4">RX</span>
                      <Sparkline
                        data={portHistory.slice(1).map((e, i) => Math.max(0, e.rx_bytes - portHistory[i].rx_bytes))}
                        color="var(--color-accent, #6366f1)"
                      />
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[10px] text-text2 w-4">TX</span>
                      <Sparkline
                        data={portHistory.slice(1).map((e, i) => Math.max(0, e.tx_bytes - portHistory[i].tx_bytes))}
                        color="var(--color-green, #22c55e)"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Device History */}
              <div className="mt-5 pt-5 border-t border-border">
                <h4 className="text-[10px] font-bold text-text2 uppercase tracking-wider mb-3">Device History</h4>
                {macHistory.length === 0 ? (
                  <div className="text-text2 italic text-[11px]">No history recorded yet</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-text2 text-[10px] uppercase tracking-wider border-b border-border">
                          <th className="pb-2 text-left">MAC</th>
                          <th className="pb-2 text-left">Vendor</th>
                          <th className="pb-2 text-left">IP</th>
                          <th className="pb-2 text-left">Hostname</th>
                          <th className="pb-2 text-left">Last Seen</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/50">
                        {macHistory.map((m) => (
                          <tr key={`${m.mac_address}-${m.last_seen}`} className="hover:bg-accent/5">
                            <td className="py-1.5 font-mono text-text pr-4">{m.mac_address}</td>
                            <td className="py-1.5 text-text2 pr-4">{m.vendor || '—'}</td>
                            <td className="py-1.5 text-accent pr-4"><span className="flex items-center">{m.ip_address || '—'}<NetworkBadge ip={m.ip_address} subnets={subnets} /></span></td>
                            <td className="py-1.5 text-white pr-4">{m.hostname || '—'}</td>
                            <td className="py-1.5 text-text2">{formatIsoDate(m.last_seen, 'datetime')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* Tooltip */}
      {tooltip && (
        <div 
          className="fixed z-50 pointer-events-none bg-[#1e2235] border border-border rounded-lg p-3 shadow-2xl text-[11px] min-w-[180px]"
          style={{ left: Math.min(tooltip.x + 16, window.innerWidth - 200), top: tooltip.y + 16 }}
        >
          <div className="font-bold text-sm mb-2 border-b border-border pb-1">{tooltip.port.port_name}</div>
          <div className="flex justify-between mb-1"><span className="text-text2">Status</span><span className={tooltip.port.oper_status === 1 ? 'text-green' : 'text-red'}>{tooltip.port.oper_status === 1 ? 'Up' : 'Down'}</span></div>
          <div className="flex justify-between mb-1"><span className="text-text2">Speed</span><span>{formatSpeed(tooltip.port.speed)}</span></div>
          {tooltip.port.lldp_neighbor && (() => {
            const ap = isApNeighbor(tooltip.port.lldp_neighbor, tooltip.port.lldp_neighbor_chassis_id, tooltip.port.lldp_neighbor_vendor);
            return (
              <div className="flex justify-between mb-1">
                <span className="text-text2">{ap ? 'Wireless AP' : 'Neighbor'}</span>
                <span className={ap ? 'text-orange-400' : 'text-blue'}>{tooltip.port.lldp_neighbor || tooltip.port.lldp_neighbor_chassis_id}</span>
              </div>
            );
          })()}
          <div className="flex justify-between mb-1"><span className="text-text2">MACs</span><span>{macEntries[tooltip.port.port_index]?.length || 0}</span></div>
          {tooltip.port.poe_draw_mw != null && tooltip.port.poe_draw_mw > 0 && (
            <div className="flex justify-between mb-1"><span className="text-text2">PoE</span><span className="text-accent2">{(tooltip.port.poe_draw_mw/1000).toFixed(1)}W</span></div>
          )}
          {tooltip.port.oper_status !== 1 && tooltip.port.last_mac && (
            <div className="mt-2 pt-2 border-t border-border/50">
              <div className="text-[9px] text-text2 uppercase font-bold mb-1">Last Device</div>
              <div className="font-mono text-[10px]">{tooltip.port.last_mac}</div>
              {tooltip.port.last_hostname && <div className="text-[10px] text-accent">{tooltip.port.last_hostname}</div>}
            </div>
          )}
          {tooltip.port.notes && (
            <div className="mt-2 pt-2 border-t border-border/50 text-[10px] text-text2 italic">{tooltip.port.notes}</div>
          )}
        </div>
      )}
    </div>
  );
}

// Keywords matched against LLDP remote system name (case-insensitive)
const AP_NAME_KEYWORDS = [
  'meraki', 'unifi', 'ubiquiti', 'aruba', 'ruckus', 'aironet',
  'fortiap', 'engenius', 'zoneflex', 'unleashed', 'lwap', 'wifi', 'wireless',
  'mist', 'juniper ap', 'cambium', 'mikrotik', 'sophos ap',
];

// Keywords matched against OUI vendor string returned by server-side lookup
const AP_OUI_VENDOR_KEYWORDS = [
  'ubiquiti', 'aruba', 'ruckus', 'aerohive', 'meraki', 'fortinet',
  'engenius', 'cambium', 'mikrotik', 'mist networks', 'juniper',
  'sophos', 'lancom',
];

// Meraki OUIs that register as "Cisco Systems" in the IEEE database.
// Normalized to lowercase "xx:xx:xx" (first 3 octets).
const MERAKI_OUI_PREFIXES = new Set([
  '00:18:0a', '88:15:44', '0c:8d:db', '34:56:fe',
  'e0:55:3d', 'a4:c3:f0', '98:18:88', 'd8:6c:02',
  'ac:17:c8', 'f8:b1:56', '68:3a:1e', 'e8:9f:80',
  '00:26:cb', '88:dc:96', 'c8:d3:a3', 'b4:e9:b0',
  'e4:55:a8', '34:bd:c8', '4c:14:bc', 'cc:4e:24',
  'f4:f5:d8', '00:de:fb', '38:0a:bc',
]);

// Normalize any MAC/chassis-ID format to "xx:xx:xx" OUI prefix.
// Handles colon-separated, dash-separated, dot-separated, or bare hex strings.
function ouiPrefix(raw: string): string {
  const hex = raw.toLowerCase().replace(/[^0-9a-f]/g, '');
  if (hex.length < 6) return '';
  return `${hex.slice(0, 2)}:${hex.slice(2, 4)}:${hex.slice(4, 6)}`;
}

// Check a single MAC (from the FDB table) for AP vendor signals.
function isApMacVendor(mac: string, vendor?: string | null): boolean {
  if (vendor) {
    const v = vendor.toLowerCase();
    if (AP_OUI_VENDOR_KEYWORDS.some(kw => v.includes(kw))) return true;
  }
  return MERAKI_OUI_PREFIXES.has(ouiPrefix(mac));
}

function isApNeighbor(
  name: string | undefined,
  chassisId?: string,
  oui_vendor?: string | null,
): boolean {
  if (name) {
    const lower = name.toLowerCase();
    if (AP_NAME_KEYWORDS.some(kw => lower.includes(kw))) return true;
    if (lower.startsWith('ap-') || lower.startsWith('uap-')) return true;
  }
  if (oui_vendor) {
    const v = oui_vendor.toLowerCase();
    if (AP_OUI_VENDOR_KEYWORDS.some(kw => v.includes(kw))) return true;
  }
  if (chassisId) {
    if (MERAKI_OUI_PREFIXES.has(ouiPrefix(chassisId))) return true;
  }
  return false;
}

function Sparkline({ data, color, width = 280, height = 60 }: { data: number[], color: string, width?: number, height?: number }) {
  if (data.length < 2) return <div className="h-[60px] flex items-center justify-center text-text2 text-xs">Not enough data</div>;
  const max = Math.max(...data, 1);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - (v / max) * (height - 4)}`).join(' ');
  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

const PORT_TYPE_DOT_COLOR: Record<string, string> = {
  AP: 'bg-cyan-400',
  Phone: 'bg-orange-400',
  Server: 'bg-purple-500',
  Printer: 'bg-gray-400',
  Workstation: 'bg-gray-400',
  Uplink: 'bg-yellow-400',
  Trunk: 'bg-yellow-400',
  Unused: 'bg-gray-400',
};

function PortBlock({ port, macs, activeVlan, isSelected, onClick, onHover }: any) {
  const isDown = port.oper_status !== 1;
  const taggedVlanCount = port.vlans.filter((v: any) => v.tagged).length;
  const hasLldpNeighbor = !!port.lldp_neighbor;
  // AP end-port: detected via LLDP neighbor name/OUI, OR by MAC OUI when the AP
  // doesn't send LLDP. Trunk guard: >3 tagged VLANs = inter-switch trunk, never AP.
  const isApByLldp = hasLldpNeighbor
    && isApNeighbor(port.lldp_neighbor, port.lldp_neighbor_chassis_id, port.lldp_neighbor_vendor);
  // MAC-based detection: count how many MACs on this port match an AP vendor OUI.
  // 1–3 matches = almost certainly a direct AP connection (AP wired port + up to 2
  // radio interfaces). 4+ matches = a trunk with multiple APs behind it — skip.
  // EXOS returns all-zeros for LLDP chassis IDs, so the LLDP path alone is unreliable
  // for Meraki APs; this MAC count approach handles both the no-LLDP and bad-chassis-ID cases.
  const apMacCount = macs.filter((m: any) => isApMacVendor(m.mac_address, m.vendor)).length;
  const isApByMac = apMacCount >= 1 && apMacCount <= 3;
  const isApPort = (isApByLldp || isApByMac) && taggedVlanCount <= 3;
  const isTrunk = !isApPort && (hasLldpNeighbor || taggedVlanCount > 1);
  const hasMacs = macs.length > 0;

  // Base status color
  let bgColor = 'bg-bg';
  let textColor = 'text-gray';
  let borderColor = 'border-border';

  if (!isDown) {
    if (isApPort) {
      bgColor = 'bg-orange-500/20';
      textColor = 'text-orange-400';
      borderColor = 'border-orange-500/50';
    } else if (isTrunk) {
      bgColor = 'bg-accent2/20';
      textColor = 'text-accent2';
      borderColor = 'border-accent2/50';
    } else if (hasMacs) {
      bgColor = 'bg-green/20';
      textColor = 'text-green';
      borderColor = 'border-green/50';
    } else {
      bgColor = 'bg-surface2';
      textColor = 'text-text2';
      borderColor = 'border-border';
    }
  }

  // VLAN Filter highlighting
  let dim = activeVlan !== null;
  let highlight = false;
  if (activeVlan !== null) {
    const v = port.vlans.find((v: any) => v.vlan_id === activeVlan);
    if (v) {
      dim = false;
      highlight = true;
    }
  }

  const label = port.port_name
    ? port.port_name.replace(/^(GigabitEthernet|FastEthernet)/i, 'Gi').replace(/^Ethernet/i, 'Et')
    : port.port_index;

  return (
    <div 
      onClick={onClick}
      onMouseEnter={(e) => onHover(e, port)}
      onMouseLeave={(e) => onHover(e, null)}
      className={`
        w-9 h-7 rounded border flex items-center justify-center text-[9px] font-bold cursor-pointer transition-all relative
        ${bgColor} ${textColor} ${borderColor}
        ${isSelected ? 'ring-2 ring-white scale-110 z-10 shadow-lg' : 'hover:scale-110 hover:z-10'}
        ${dim ? 'opacity-10 grayscale' : 'opacity-100'}
        ${highlight ? 'ring-2 ring-blue border-blue shadow-[0_0_8px_rgba(59,130,246,0.5)] z-10' : ''}
      `}
    >
      {label}
      {isTrunk && <div className="absolute top-0 right-0.5 text-[6px]">T</div>}
      {isApPort && <div className="absolute top-0 right-0.5 text-[6px]">W</div>}
      {port.flap_count > 0 && <div className="absolute bottom-0 left-0.5 text-[6px] text-yellow">!</div>}
      {port.port_type && (
        <div className={`absolute bottom-0.5 right-0.5 w-1.5 h-1.5 rounded-full ${PORT_TYPE_DOT_COLOR[port.port_type] || 'bg-gray-400'}`} />
      )}
    </div>
  );
}

function LegendItem({ color, label }: { color: string, label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-3 h-3 rounded-sm ${color}`} />
      <span>{label}</span>
    </div>
  );
}

function DetailRow({ label, value, color }: { label: string, value: string, color?: string }) {
  return (
    <div className="flex justify-between items-center py-1 border-b border-border/50 text-[11px]">
      <span className="text-text2">{label}</span>
      <span className={`font-medium ${color || 'text-text'}`}>{value}</span>
    </div>
  );
}

function PortNotesEditor({ portId, deviceId, initialNotes, apiFetch, onSave }: {
  portId: number;
  deviceId: number;
  initialNotes: string;
  apiFetch: (url: string, options?: any) => Promise<Response>;
  onSave?: (notes: string) => void;
}) {
  const [notes, setNotes] = useState(initialNotes);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await apiFetch(`/api/devices/${deviceId}/ports/${portId}/notes`, {
        method: 'PATCH',
        body: JSON.stringify({ notes }),
      });
      onSave?.(notes);
    } catch (e) {
      console.error('Failed to save notes', e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <textarea
      className="w-full bg-bg border border-border rounded p-2 text-[11px] text-text resize-none focus:outline-none focus:border-accent transition-colors"
      rows={3}
      placeholder="Add port notes..."
      value={notes}
      onChange={(e) => setNotes(e.target.value)}
      onBlur={save}
    />
  );
}
