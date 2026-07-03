import { useState, useRef, useEffect, type ReactNode } from 'react';
import { Search as SearchIcon, MapPin, ArrowRight, Activity, Zap, ExternalLink, RefreshCw, Tag } from 'lucide-react';
import { Network as VisNetwork, DataSet } from 'vis-network/standalone';
import NetworkBadge from '../components/NetworkBadge';
import { SubnetDef, formatIsoDate } from '../utils';
import { LabelEntry } from '../App';

interface SearchResult {
  mac_address: string;
  device_id: number;
  device_hostname: string;
  device_ip: string;
  port_id?: number;
  port_name?: string;
  port_index?: number;
  vlan_id?: number;
  ip_address?: string;
  end_host_hostname?: string;
  port_mac_count?: number;
  last_seen?: string;
  lldp_neighbor?: string;
  _seenOn?: number; // helper for deduping
}

interface PathGroup {
  device_id: number;
  device_hostname: string;
  device_ip: string;
  path: number[];
  results: SearchResult[];
}

export default function SearchView({ onJumpToFaceplate, topologyData, apiFetch, globalQuery, subnets = [], labels = new Map(), onLabelMac }: {
  onJumpToFaceplate: (deviceId: number, portIndex?: number) => void,
  topologyData: { nodes: any[], edges: any[] },
  apiFetch: (url: string, options?: any) => Promise<Response>,
  globalQuery: string,
  subnets?: SubnetDef[],
  labels?: Map<string, LabelEntry>,
  onLabelMac?: (mac: string) => void,
}) {
  const [results, setResults] = useState<PathGroup[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedResult, setSelectedResult] = useState<{group: PathGroup, item: SearchResult} | null>(null);
  const pathNetworkRef = useRef<VisNetwork | null>(null);
  const pathContainerRef = useRef<HTMLDivElement>(null);

  // Auto-search when globalQuery changes
  useEffect(() => {
    const timer = setTimeout(() => {
      if (globalQuery.trim().length >= 2) {
        doSearch(globalQuery);
      } else if (globalQuery.trim().length === 0) {
        setResults([]);
      }
    }, 300); // Small debounce
    return () => clearTimeout(timer);
  }, [globalQuery]);

  const getPathFromGateway = (deviceId: number) => {
    if (!topologyData || !topologyData.nodes) return [deviceId];
    
    const target = topologyData.nodes.find(n => n.id === deviceId);
    const gateway = topologyData.nodes.find(n => n.is_gateway && n.site === (target && target.site))
                 || topologyData.nodes.find(n => n.is_gateway);
    
    if (!gateway || gateway.id === deviceId) return [deviceId];
    
    const adj: Record<number, number[]> = {};
    if (topologyData.edges) {
      topologyData.edges.forEach(e => {
        (adj[e.source_device_id] = adj[e.source_device_id] || []).push(e.target_device_id);
        (adj[e.target_device_id] = adj[e.target_device_id] || []).push(e.source_device_id);
      });
    }

    const prev: Record<number, number | null> = { [gateway.id]: null };
    const queue = [gateway.id];
    let found = false;

    while (queue.length > 0) {
      const cur = queue.shift()!;
      if (cur === deviceId) { found = true; break; }
      for (const nb of (adj[cur] || [])) {
        if (!(nb in prev)) { prev[nb] = cur; queue.push(nb); }
      }
    }

    if (!found) return [deviceId];
    const path = [];
    let curr: number | null = deviceId;
    while (curr !== null) { path.unshift(curr); curr = prev[curr]; }
    return path;
  };

  const doSearch = async (q: string) => {
    setIsLoading(true);
    setSelectedResult(null);
    try {
      const res = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);
      const raw: SearchResult[] = await res.json();
      
      // Access port logic: group by MAC and pick the best candidate
      const byMac: Record<string, SearchResult[]> = {};
      const deviceOnly: SearchResult[] = [];
      raw.forEach(r => {
        if (!r.mac_address) { deviceOnly.push(r); return; }
        (byMac[r.mac_address] = byMac[r.mac_address] || []).push(r);
      });

      const deduped = [...deviceOnly];
      
      // Get a set of managed switch names to identify internal trunks
      const nodes = topologyData?.nodes || [];
      const managedNames = new Set(
        nodes
          .filter(n => !n.unmanaged)
          .flatMap(n => [n.hostname.toLowerCase(), (n.snmp_name || '').toLowerCase()])
          .filter(Boolean)
      );

      Object.values(byMac).forEach(candidates => {
        // FILTER: Remove results on trunks connecting to OTHER MANAGED SWITCHES
        // Keep results on ports with NO neighbor OR an unmanaged neighbor (like an AP)
        const accessPortCandidates = candidates.filter(r => {
          if (!r.lldp_neighbor) return true;
          // If neighbor name isn't in our managed list, it's likely an AP/Phone/Printer - keep it!
          return !managedNames.has(r.lldp_neighbor.toLowerCase());
        });

        // Use the best candidate from the filtered pool
        const pool = accessPortCandidates.length > 0 ? accessPortCandidates : candidates;
        
        const best = pool.reduce((prev, cur) => 
          (cur.port_mac_count ?? 999) < (prev.port_mac_count ?? 999) ? cur : prev
        );
        
        best._seenOn = candidates.length;
        deduped.push(best);
      });

      // Group by device + calculate path
      const groups: Record<number, PathGroup> = {};
      deduped.forEach(r => {
        if (!groups[r.device_id]) {
          groups[r.device_id] = {
            device_id: r.device_id,
            device_hostname: r.device_hostname,
            device_ip: r.device_ip,
            path: getPathFromGateway(r.device_id),
            results: []
          };
        }
        groups[r.device_id].results.push(r);
      });

      setResults(Object.values(groups).sort((a, b) => a.path.length - b.path.length));
    } catch (e) {
      console.error("Search failed", e);
    } finally {
      setIsLoading(false);
    }
  };

  // Draw path graph when a result is selected
  useEffect(() => {
    if (!selectedResult || !pathContainerRef.current || !topologyData || !topologyData.nodes) return;

    const { group } = selectedResult;
    const nodeMap = Object.fromEntries(topologyData.nodes.map(n => [n.id, n]));
    
    const visNodes = group.path.map((id, i) => {
      const node = nodeMap[id] || {};
      const isGW = node.is_gateway;
      const isTgt = id === group.device_id;
      return {
        id, level: i,
        label: (node.snmp_name || node.hostname || `Device ${id}`).replace(/\s+/g, '\n'),
        shape: 'box', margin: 10,
        font: { color: '#e2e8f0', size: 12 },
        color: isGW ? '#064e3b' : isTgt ? '#1e1b4b' : '#1e293b',
        borderWidth: isTgt ? 2 : 1,
        borderColor: isGW ? '#10b981' : isTgt ? '#818cf8' : '#334155',
      };
    });

    const visEdges = [];
    const edges = topologyData.edges || [];
    for (let i = 0; i < group.path.length - 1; i++) {
      const [a, b] = [group.path[i], group.path[i+1]];
      const e = edges.find(e => 
        (e.source_device_id === a && e.target_device_id === b) || 
        (e.source_device_id === b && e.target_device_id === a));
      
      const sp = e?.source_device_id === a ? e.source_port : e?.target_port;
      const tp = e?.source_device_id === b ? e.source_port : e?.target_port;

      visEdges.push({
        from: a, to: b,
        label: [sp, tp].filter(Boolean).join('\n'),
        font: { color: '#94a3b8', size: 10, strokeWidth: 0 },
        smooth: false,
        color: '#475569',
        arrows: { to: { enabled: true, scaleFactor: 0.5 } }
      });
    }

    const options = {
      layout: { hierarchical: { direction: 'UD', sortMethod: 'directed', levelSeparation: 100 } },
      physics: false,
      interaction: { dragNodes: false, zoomView: false, dragView: true }
    };

    if (pathNetworkRef.current) pathNetworkRef.current.destroy();
    pathNetworkRef.current = new VisNetwork(pathContainerRef.current, { nodes: new DataSet(visNodes) as any, edges: new DataSet(visEdges) as any } as any, options);

    return () => { if (pathNetworkRef.current) pathNetworkRef.current.destroy(); };
  }, [selectedResult, topologyData]);

  return (
    <div className="flex h-full p-6 gap-6 overflow-hidden">
      {/* Left Search Results */}
      <div className="flex-1 flex flex-col gap-4 overflow-hidden">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-bold">Network Search</h2>
          <p className="text-xs text-text2">
            Showing results for <span className="text-accent font-bold">"{globalQuery}"</span>
          </p>
        </div>

        <div className="flex-1 overflow-y-auto pr-2 space-y-4">
          {isLoading && (
            <div className="h-40 flex flex-col items-center justify-center text-text2 border border-dashed border-border rounded-xl">
              <RefreshCw className="w-8 h-8 mb-2 animate-spin text-accent" />
              <p className="text-sm">Searching the fabric...</p>
            </div>
          )}

          {!isLoading && results.length === 0 && globalQuery.length > 0 && (
            <div className="h-40 flex flex-col items-center justify-center text-text2 border border-dashed border-border rounded-xl">
              <MapPin className="w-8 h-8 mb-2 opacity-20" />
              <p className="text-sm font-medium">No active connections found for this query.</p>
            </div>
          )}

          {!isLoading && results.length === 0 && globalQuery.length === 0 && (
            <div className="h-40 flex flex-col items-center justify-center text-text2 border border-dashed border-border rounded-xl">
              <SearchIcon className="w-8 h-8 mb-2 opacity-20" />
              <p className="text-sm">Start typing in the toolbar to search the network.</p>
            </div>
          )}

          {!isLoading && results.map((group) => (
            <div key={group.device_id} className="bg-surface border border-border rounded-xl overflow-hidden shadow-sm">
              <div className="px-4 py-3 bg-surface2 border-b border-border flex justify-between items-center">
                <div>
                  <h3 className="text-xs font-bold text-white uppercase tracking-wider">{group.device_hostname}</h3>
                  <div className="flex items-center gap-2 mt-1">
                    <div className="flex items-center text-[10px] text-text2 gap-1 font-mono">
                      {group.path.map((id, i) => {
                        const node = (topologyData?.nodes || []).find(n => n.id === id);
                        const label = node ? (node.snmp_name || node.hostname) : `Switch ${id}`;
                        return (
                          <span key={id} className="flex items-center gap-1">
                            {i > 0 && <ArrowRight className="w-2.5 h-2.5 opacity-40" />}
                            <span className={id === group.device_id ? 'text-accent font-bold' : ''}>
                              {label}
                            </span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                </div>
                <div className="text-[10px] text-text2 font-mono bg-bg px-2 py-1 rounded border border-border">
                  {group.device_ip}
                </div>
              </div>

              <div className="divide-y divide-border">
                {group.results.map((item) => (
                  <div
                    key={`${item.mac_address}-${item.port_index}`}
                    onClick={() => setSelectedResult({ group, item })}
                    className={`px-4 py-3 flex items-center gap-4 cursor-pointer hover:bg-surface2 transition-colors ${
                      selectedResult?.item === item ? 'bg-accent/5 border-l-2 border-accent' : ''
                    }`}
                  >
                    <div className="flex items-center gap-1.5 w-40 shrink-0">
                      <span className="font-mono text-sm text-white truncate">{item.mac_address || '—'}</span>
                      {item.mac_address && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onLabelMac?.(item.mac_address); }}
                          title={labels.get(item.mac_address) ? `Edit: ${labels.get(item.mac_address)!.label}` : 'Add MAC name'}
                          className="text-text2 hover:text-accent transition-colors flex-shrink-0"
                        >
                          <Tag className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                    <div className="flex-1 flex flex-col gap-0.5">
                      <div className="flex items-center gap-2">
                        {item.mac_address && labels.get(item.mac_address) && (
                          <span className="px-1.5 py-0.5 bg-accent/10 border border-accent/20 text-accent text-[10px] rounded font-medium">
                            {labels.get(item.mac_address)!.label}
                          </span>
                        )}
                        {item.end_host_hostname && <span className="text-accent text-xs font-bold">{item.end_host_hostname}</span>}
                        <span className="text-text2 text-[11px]">{item.port_name || 'Port ' + item.port_index}</span>
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-text2">
                        {item.ip_address && <span className="flex items-center text-text">{item.ip_address}<NetworkBadge ip={item.ip_address} subnets={subnets} /></span>}
                        {item.vlan_id && <span>· VLAN {item.vlan_id}</span>}
                        {item.last_seen && <span>· Seen {formatIsoDate(item.last_seen, 'date')}</span>}
                        {item._seenOn && item._seenOn > 1 && <span className="text-accent2">· Visible on {item._seenOn} switches</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Right Sidebar - Path & Mini-View */}
      <div className={`w-[400px] bg-surface border border-border rounded-xl flex flex-col overflow-hidden transition-all ${
        selectedResult ? 'translate-x-0 opacity-100' : 'translate-x-8 opacity-0 pointer-events-none'
      }`}>
        <div className="p-4 border-b border-border bg-surface2 flex justify-between items-center">
          <h3 className="text-sm font-bold flex items-center gap-2">
            <MapPin className="w-4 h-4 text-accent" />
            Location Detail
          </h3>
          <button onClick={() => setSelectedResult(null)} className="text-text2 hover:text-white">✕</button>
        </div>

        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Path Graph */}
          <div className="h-64 bg-bg border-b border-border relative">
            <div className="absolute top-2 left-2 text-[10px] text-text2 uppercase tracking-widest font-bold px-2 py-1 bg-surface/50 rounded pointer-events-none">Path from Gateway</div>
            <div ref={pathContainerRef} className="h-full w-full" />
          </div>

          <div className="p-5 flex-1 overflow-y-auto space-y-6">
            <div className="space-y-4">
              <h4 className="text-[10px] font-bold text-text2 uppercase tracking-widest border-b border-border pb-2">Target Device</h4>
              <div className="space-y-2">
                <InfoRow label="MAC Address" value={
                  <div className="flex items-center gap-2">
                    <span className="font-mono">{selectedResult?.item.mac_address || '—'}</span>
                    {selectedResult?.item.mac_address && (
                      <button onClick={() => onLabelMac?.(selectedResult.item.mac_address)} title="Add/edit MAC name" className="text-text2 hover:text-accent transition-colors">
                        <Tag className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                } />
                {selectedResult?.item.mac_address && labels.get(selectedResult.item.mac_address) && (
                  <InfoRow label="Name" value={labels.get(selectedResult.item.mac_address)!.label} color="text-accent" />
                )}
                <InfoRow label="IP Address" value={selectedResult?.item.ip_address || '—'} badge={<NetworkBadge ip={selectedResult?.item.ip_address} subnets={subnets} />} />
                <InfoRow label="Hostname" value={selectedResult?.item.end_host_hostname || '—'} color="text-accent" />
                <InfoRow label="Switch Port" value={selectedResult?.item.port_name || '—'} />
                <InfoRow label="VLAN" value={selectedResult?.item.vlan_id?.toString() || '—'} />
              </div>
            </div>

            <div className="pt-4 space-y-2">
              <button 
                onClick={() => selectedResult && onJumpToFaceplate(selectedResult.item.device_id, selectedResult.item.port_index)}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-accent text-white rounded-lg text-sm font-medium hover:opacity-90 transition-opacity shadow-lg shadow-accent/20"
              >
                Jump to Faceplate <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono, color, badge }: { label: string, value: ReactNode, mono?: boolean, color?: string, badge?: ReactNode }) {
  return (
    <div className="flex justify-between items-center text-xs">
      <span className="text-text2">{label}</span>
      <span className={`flex items-center ${mono ? 'font-mono' : 'font-medium'} ${color || 'text-white'}`}>{value}{badge}</span>
    </div>
  );
}
