import { useEffect, useRef, useState, useMemo } from 'react';
import { Network as VisNetwork, DataSet } from 'vis-network/standalone';
import { Info, Activity, Globe, Shield, ExternalLink, RefreshCw } from 'lucide-react';
import { escHtml } from '../utils';

interface Node {
  id: number;
  hostname: string;
  snmp_name?: string;
  ip_address: string;
  vendor?: string;
  model?: string;
  poll_status: string;
  last_polled?: string;
  is_gateway?: boolean;
  unmanaged?: boolean;
  site?: string;
}

interface Edge {
  id: number;
  source_device_id: number;
  target_device_id: number;
  source_port?: string;
  target_port?: string;
  remote_system_name?: string;
  down?: boolean;
}

interface TopologyData {
  nodes: Node[];
  edges: Edge[];
}

export default function TopologyView({ apiFetch, onJumpToFaceplate }: {
  apiFetch: (url: string, options?: any) => Promise<Response>;
  onJumpToFaceplate: (deviceId: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<VisNetwork | null>(null);
  const [data, setData] = useState<TopologyData>({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showUnmanaged, setShowUnmanaged] = useState(false);
  const [siteFilter, setSiteFilter] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isPollPending, setIsPollPending] = useState(false);

  const handlePollDevice = async () => {
    if (!selectedNode || isPollPending) return;
    setIsPollPending(true);
    try {
      await apiFetch(`/api/devices/${selectedNode.id}/poll`, { method: 'POST' });
      setTimeout(() => { fetchTopology(); setIsPollPending(false); }, 4000);
    } catch (e) {
      console.error('Poll failed', e);
      setIsPollPending(false);
    }
  };

  const fetchTopology = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/topology');
      const json = await res.json();
      setData(json);
    } catch (e) {
      console.error("Failed to fetch topology", e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTopology();
  }, []);

  const sites = useMemo(() => {
    const s = new Set(data.nodes.map(n => n.site).filter(Boolean));
    return Array.from(s).sort();
  }, [data.nodes]);

  // Re-draw when data or filters change
  useEffect(() => {
    if (!containerRef.current || data.nodes.length === 0) return;

    let filteredNodes = data.nodes;
    if (!showUnmanaged) filteredNodes = filteredNodes.filter(n => !n.unmanaged);
    if (siteFilter) filteredNodes = filteredNodes.filter(n => n.site === siteFilter);

    const nodeIds = new Set(filteredNodes.map(n => n.id));
    const filteredEdges = data.edges.filter(e => 
      nodeIds.has(e.source_device_id) && nodeIds.has(e.target_device_id)
    );

    if (filteredNodes.length === 0) {
      if (networkRef.current) networkRef.current.destroy();
      return;
    }

    // ── Level Calculation (Gateways at Top) ─────────────────────────────────
    const nodeLevels: Record<number, number> = {};
    const gateways = filteredNodes.filter(n => n.is_gateway);
    
    // BFS to determine levels starting from gateways
    const adj: Record<number, number[]> = {};
    filteredEdges.forEach(e => {
      (adj[e.source_device_id] = adj[e.source_device_id] || []).push(e.target_device_id);
      (adj[e.target_device_id] = adj[e.target_device_id] || []).push(e.source_device_id);
    });

    const queue: [number, number][] = [];
    
    // Initialize gateways at Level 0
    gateways.forEach(gw => {
      nodeLevels[gw.id] = 0;
      queue.push([gw.id, 0]);
    });

    // If no gateways, start BFS from all root-like nodes or just everything at level 0
    if (gateways.length === 0 && filteredNodes.length > 0) {
      filteredNodes.forEach(n => {
        nodeLevels[n.id] = 0;
        queue.push([n.id, 0]);
      });
    }

    // Standard BFS
    while (queue.length > 0) {
      const [u, level] = queue.shift()!;
      (adj[u] || []).forEach(v => {
        if (!(v in nodeLevels)) {
          nodeLevels[v] = level + 1;
          queue.push([v, level + 1]);
        }
      });
    }

    // Assign max level to any orphaned nodes not reached by BFS
    const maxKnownLevel = Math.max(0, ...Object.values(nodeLevels));
    filteredNodes.forEach(n => {
      if (!(n.id in nodeLevels)) {
        nodeLevels[n.id] = maxKnownLevel + 1;
      }
    });

    const visNodes = filteredNodes.map(n => {
      const statusColor = n.poll_status === 'ok' ? '#22c55e' : n.poll_status === 'degraded' ? '#eab308' : n.poll_status === 'error' ? '#ef4444' : '#6b7280';
      
      if (n.unmanaged) {
        return {
          id: n.id,
          label: n.hostname,
          shape: 'ellipse',
          level: nodeLevels[n.id],
          color: { background: '#1e2130', border: '#475569' },
          font: { color: '#94a3b8', size: 11 },
          borderWidth: 1,
          borderDashes: [4, 3],
        };
      }

      return {
        id: n.id,
        label: `${n.is_gateway ? '⬡ ' : ''}${n.snmp_name || n.hostname}\n${n.ip_address}`,
        shape: 'box',
        level: nodeLevels[n.id],
        color: {
          background: n.is_gateway ? '#1a2e1a' : '#1a1d27',
          border: statusColor,
          highlight: { background: '#22263a', border: statusColor }
        },
        font: { color: '#e2e8f0', size: 12 },
        borderWidth: n.is_gateway ? 3 : 2,
        margin: 10
      };
    });

    const visEdges = filteredEdges.map(e => ({
      id: e.id,
      from: e.source_device_id,
      to: e.target_device_id,
      color: e.down ? { color: '#ef4444', opacity: 0.9 } : { color: '#4f8ef7', opacity: 0.6 },
      dashes: e.down ? [6, 6] : false,
      width: e.down ? 2.5 : 2,
      smooth: false
    }));

    const options = {
      layout: {
        hierarchical: {
          enabled: true,
          direction: 'UD',
          sortMethod: 'directed',
          levelSeparation: 150,
          nodeSpacing: 250,
          parentCentralization: true,
          blockShifting: true,
          edgeMinimization: true
        }
      },
      physics: { enabled: false },
      interaction: { hover: true, tooltipDelay: 200, dragNodes: true },
    };

    try {
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
      }
      
      const network = new VisNetwork(
        containerRef.current, 
        { nodes: new DataSet(visNodes) as any, edges: new DataSet(visEdges) as any } as any, 
        options
      );
      networkRef.current = network;

      network.on('click', (params) => {
        if (params.nodes.length > 0) {
          const id = params.nodes[0];
          const node = filteredNodes.find(n => n.id === id);
          setSelectedNode(node || null);
        } else {
          setSelectedNode(null);
        }
      });

      // Custom drawing for port labels
      network.on('afterDrawing', (ctx) => {
        const positions = network.getPositions();
        ctx.save();
        ctx.font = 'bold 10px Inter, sans-serif';
        ctx.textAlign = 'center';
        
        filteredEdges.forEach(edge => {
          const from = positions[edge.source_device_id];
          const to = positions[edge.target_device_id];
          if (!from || !to) return;

          const dx = to.x - from.x;
          const dy = to.y - from.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          const ux = dx / len;
          const uy = dy / len;
          
          let px = -uy;
          let py = ux;
          if (py > 0) { px = -px; py = -py; }
          
          const offset = 15;
          const inset = Math.min(60 / len, 0.4);

          const drawLabel = (text: string, t: number) => {
            if (!text) return;
            const x = from.x + dx * t + px * offset;
            const y = from.y + dy * t + py * offset;
            const w = ctx.measureText(text).width;
            ctx.fillStyle = 'rgba(15, 17, 23, 0.8)';
            ctx.fillRect(x - w/2 - 4, y - 8, w + 8, 16);
            ctx.fillStyle = '#94a3b8';
            ctx.fillText(text, x, y + 3);
          };

          drawLabel(edge.source_port || '', inset);
          drawLabel(edge.target_port || '', 1 - inset);
        });
        ctx.restore();
      });
    } catch (err) {
      console.error("Vis-Network initialization failed", err);
    }

    return () => {
      if (networkRef.current) networkRef.current.destroy();
    };
  }, [data, showUnmanaged, siteFilter]);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 relative flex flex-col">
        {/* Sub-toolbar */}
        <div className="absolute top-4 left-4 z-10 flex gap-3 bg-surface/80 backdrop-blur-md border border-border p-2 rounded-lg shadow-xl">
          <label className="flex items-center gap-2 px-2 text-xs font-bold text-text2 cursor-pointer hover:text-text transition-colors uppercase tracking-tight">
            <input 
              type="checkbox" 
              checked={showUnmanaged} 
              onChange={e => setShowUnmanaged(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-border bg-bg text-accent focus:ring-accent"
            />
            Show LLDP Neighbors
          </label>
          <div className="w-px h-4 bg-border self-center" />
          <select 
            value={siteFilter} 
            onChange={e => setSiteFilter(e.target.value)}
            className="bg-transparent text-xs text-text2 focus:outline-none cursor-pointer hover:text-text transition-colors"
          >
            <option value="">All Sites</option>
            {sites.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <button 
            onClick={fetchTopology}
            className="p-1 hover:text-accent transition-colors"
            title="Refresh Topology"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <div ref={containerRef} className="flex-1" />
      </div>

      {/* Sidebar */}
      {selectedNode && (
        <div className="w-80 bg-surface border-l border-border flex flex-col animate-in slide-in-from-right duration-200">
          <div className="p-5 border-b border-border flex justify-between items-start">
            <div>
              <h3 className="font-bold text-white">{selectedNode.snmp_name || selectedNode.hostname}</h3>
              <p className="text-xs text-text2 mt-1">{selectedNode.ip_address}</p>
            </div>
            <button onClick={() => setSelectedNode(null)} className="text-text2 hover:text-white">✕</button>
          </div>

          <div className="p-5 flex-1 overflow-y-auto space-y-6">
            <div className="space-y-3">
              <SidebarRow icon={<Activity className="w-3.5 h-3.5" />} label="Status" value={
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                  selectedNode.poll_status === 'ok' ? 'bg-green/20 text-green' : 'bg-red/20 text-red'
                }`}>{selectedNode.poll_status}</span>
              } />
              <SidebarRow icon={<Globe className="w-3.5 h-3.5" />} label="Site" value={selectedNode.site || '—'} />
              <SidebarRow icon={<Shield className="w-3.5 h-3.5" />} label="Vendor" value={selectedNode.vendor || '—'} />
              <SidebarRow icon={<Info className="w-3.5 h-3.5" />} label="Model" value={selectedNode.model || '—'} />
            </div>

            {!selectedNode.unmanaged && (
              <div className="space-y-2 pt-4 border-top border-border">
                <button
                  onClick={() => onJumpToFaceplate(selectedNode.id)}
                  className="w-full flex items-center justify-center gap-2 py-2 bg-surface2 border border-border rounded-md text-xs font-medium hover:border-accent hover:text-accent transition-all"
                >
                   View Faceplate <ExternalLink className="w-3 h-3" />
                </button>
                <button
                  onClick={handlePollDevice}
                  disabled={isPollPending}
                  className="w-full flex items-center justify-center gap-2 py-2 bg-surface2 border border-border rounded-md text-xs font-medium hover:border-accent hover:text-accent transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                >
                   {isPollPending ? 'Polling…' : 'Poll Device Now'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SidebarRow({ icon, label, value }: any) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border/30 text-xs">
      <div className="flex items-center gap-2 text-text2">
        {icon}
        <span>{label}</span>
      </div>
      <div className="font-medium text-text">{value}</div>
    </div>
  );
}
