import { useState, useEffect, useCallback } from 'react';
import {
  Network,
  Search,
  LayoutGrid,
  BarChart3,
  Settings,
  Bell,
  Moon,
  Sun,
  RefreshCw,
  Zap,
  LogOut,
  X,
  Tag
} from 'lucide-react';

import AuthView from './views/AuthView';
import TopologyView from './views/TopologyView';
import FaceplateView from './views/FaceplateView';
import SearchView from './views/SearchView';
import DashboardView from './views/DashboardView';
import EventsView from './views/EventsView';
import SettingsView from './views/SettingsView';
import MacNamesView from './views/MacNamesView';
import SidebarOverlay from './components/SidebarOverlay';
import { SubnetDef } from './utils';

export interface LabelEntry {
  id: number;
  mac_address: string;
  vendor?: string | null;
  label: string;
  notes?: string | null;
}

export default function App() {
  const [activeTab, setActiveTab] = useState(() => {
    const [tab] = window.location.hash.slice(1).split('/');
    return ['dashboard', 'topology', 'faceplate', 'search', 'macnames'].includes(tab) ? tab : 'dashboard';
  });
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'dark');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isEventsOpen, setIsEventsOpen] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [topologyData, setTopologyData] = useState({ nodes: [], edges: [] });
  const [jumpParams, setJumpParams] = useState<{deviceId: number, portIndex?: number} | null>(() => {
    const [tab, rawId, rawPort] = window.location.hash.slice(1).split('/');
    if (tab !== 'faceplate' || !rawId) return null;
    const deviceId = parseInt(rawId);
    if (isNaN(deviceId)) return null;
    const portIndex = rawPort ? parseInt(rawPort) : undefined;
    return { deviceId, portIndex: portIndex !== undefined && !isNaN(portIndex) ? portIndex : undefined };
  });
  const [refreshKey, setRefreshKey] = useState(0);
  const [faceplateNav, setFaceplateNav] = useState<{ deviceId: number | null, portIndex: number | null }>(() => {
    const [tab, rawId, rawPort] = window.location.hash.slice(1).split('/');
    if (tab !== 'faceplate' || !rawId) return { deviceId: null, portIndex: null };
    const deviceId = parseInt(rawId);
    if (isNaN(deviceId)) return { deviceId: null, portIndex: null };
    const portIndex = rawPort ? parseInt(rawPort) : null;
    return { deviceId, portIndex: portIndex !== null && !isNaN(portIndex) ? portIndex : null };
  });
  const [token, setToken] = useState<string | null>(localStorage.getItem('token'));
  const [globalSearch, setGlobalSearch] = useState('');
  const [subnets, setSubnets] = useState<SubnetDef[]>([]);
  const [labels, setLabels] = useState<Map<string, LabelEntry>>(new Map());
  const [labelPrefill, setLabelPrefill] = useState<string | null>(null);

  useEffect(() => {
    // Apply theme on load
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  // Keep URL hash in sync so browser refresh and back button work
  useEffect(() => {
    if (!token) return;
    const parts: string[] = [activeTab];
    if (activeTab === 'faceplate' && faceplateNav.deviceId) {
      parts.push(String(faceplateNav.deviceId));
      if (faceplateNav.portIndex !== null) parts.push(String(faceplateNav.portIndex));
    }
    window.location.hash = parts.join('/');
  }, [activeTab, faceplateNav, token]);

  const refreshLabels = async () => {
    try {
      const res = await apiFetch('/api/labels');
      if (res.ok) {
        const data: LabelEntry[] = await res.json();
        setLabels(new Map(data.map(l => [l.mac_address, l])));
      }
    } catch (e) {}
  };

  useEffect(() => {
    if (!token) return;
    fetchStatus();
    fetchTopology();
    fetchSubnets();
    refreshLabels();
    const interval = setInterval(() => {
      fetchStatus();
      fetchTopology();
    }, 30000);
    return () => clearInterval(interval);
  }, [token]);

  // Live updates: hold an SSE stream open while the tab is visible.
  // The server only runs fast polls while at least one stream is connected,
  // so closing on hidden tabs directly reduces SNMP traffic.
  useEffect(() => {
    if (!token) return;
    let es: EventSource | null = null;
    let debounce: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const refresh = () => {
      setRefreshKey(k => k + 1);
      fetchStatus();
      fetchTopology();
    };

    const openStream = async () => {
      if (es || closed || document.hidden) return;
      try {
        // Tickets are single-use and short-lived — fetch a fresh one per connect
        const res = await apiFetch('/api/stream/ticket', { method: 'POST' });
        if (!res.ok) return;
        const { ticket } = await res.json();
        if (closed || document.hidden) return;
        es = new EventSource(`/api/stream?ticket=${encodeURIComponent(ticket)}`);
        es.onmessage = (e) => {
          let ev: any = null;
          try { ev = JSON.parse(e.data); } catch { return; }
          if (debounce) clearTimeout(debounce);
          if (ev.type === 'cycle_complete') {
            refresh();
          } else {
            debounce = setTimeout(refresh, 2000);
          }
        };
        es.onerror = () => {
          // Built-in reconnect would replay the consumed ticket — reconnect manually
          es?.close(); es = null;
          if (!closed && !document.hidden) setTimeout(openStream, 5000);
        };
      } catch {}
    };
    const closeStream = () => { es?.close(); es = null; };
    const onVisibility = () => { document.hidden ? closeStream() : openStream(); };

    openStream();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      closed = true;
      document.removeEventListener('visibilitychange', onVisibility);
      closeStream();
      if (debounce) clearTimeout(debounce);
    };
  }, [token, apiFetch]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleLabelMac = (mac: string) => {
    setLabelPrefill(mac);
    setActiveTab('macnames');
  };

  const handleSearchChange = (val: string) => {
    setGlobalSearch(val);
    if (val.trim().length > 0 && activeTab !== 'search') {
      setActiveTab('search');
    }
  };

  const apiFetch = useCallback(async (url: string, options: any = {}) => {
    const authOptions = {
      ...options,
      headers: {
        ...options.headers,
        'Authorization': `Bearer ${token}`,
        'Content-Type': options.body instanceof FormData ? undefined : 'application/json'
      }
    };
    // Let browser set content-type for FormData
    if (options.body instanceof FormData) {
      delete (authOptions.headers as any)['Content-Type'];
    }

    const res = await fetch(url, authOptions);
    if (res.status === 401) {
      handleLogout();
      throw new Error("Session expired");
    }
    return res;
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchStatus = async () => {
    try {
      const res = await apiFetch('/api/status');
      const data = await res.json();
      setStatus(data);
    } catch (e) {
      console.error("Failed to fetch status", e);
    }
  };

  const fetchSubnets = async () => {
    try {
      const res = await apiFetch('/api/subnets');
      const data = await res.json();
      setSubnets(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error("Failed to fetch subnets", e);
    }
  };

  const fetchTopology = async () => {
    try {
      const res = await apiFetch('/api/topology');
      const data = await res.json();
      setTopologyData(data);
    } catch (e) {
      console.error("Failed to fetch topology", e);
    }
  };

  const handleLogin = (newToken: string) => {
    localStorage.setItem('token', newToken);
    setToken(newToken);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setToken(null);
  };

  const handleFaceplateNavChange = (deviceId: number | null, portIndex: number | null) => {
    setFaceplateNav({ deviceId, portIndex });
  };

  const handleJumpToFaceplate = (deviceId: number, portIndex?: number) => {
    setJumpParams({ deviceId, portIndex });
    setActiveTab('faceplate');
    setFaceplateNav({ deviceId, portIndex: portIndex ?? null });
  };

  const toggleTheme = () => {
    const newTheme = theme === 'dark' ? 'light' : 'dark';
    setTheme(newTheme);
    localStorage.setItem('theme', newTheme);
  };

  if (!token) {
    return <AuthView onLogin={handleLogin} />;
  }

  return (
    <div className={`flex flex-col h-screen bg-bg text-text font-sans selection:bg-accent/30 ${theme}`}>
      {/* Header */}
      <header className="flex items-center gap-4 px-5 py-2.5 bg-surface border-b border-border flex-shrink-0 z-40">
        <div className="flex items-center gap-2">
          <Zap className="w-5 h-5 text-accent" fill="currentColor" />
          <h1 className="text-base font-semibold tracking-tight text-white">NetBeacon</h1>
        </div>
        
        <span className="text-[11px] bg-accent2 text-white px-2 py-0.5 rounded-full font-medium">
          SNMP
        </span>
        
        <div className="text-xs text-text2 ml-3">
          {status ? (
            `${status.devices_ok}/${status.devices_total} OK · Last poll: ${status.last_poll_time ? new Date(status.last_poll_time + 'Z').toLocaleTimeString() : 'Never'}`
          ) : (
            'Loading status...'
          )}
        </div>

        <nav className="flex gap-1 ml-auto">
          <NavButton
            active={activeTab === 'macnames'}
            onClick={() => setActiveTab('macnames')}
            icon={<Tag className="w-4 h-4" />}
            label="MAC Names"
          />
          <NavButton
            active={activeTab === 'dashboard'}
            onClick={() => setActiveTab('dashboard')}
            icon={<BarChart3 className="w-4 h-4" />}
            label="Dashboard"
          />
          <NavButton
            active={activeTab === 'topology'}
            onClick={() => setActiveTab('topology')}
            icon={<Network className="w-4 h-4" />}
            label="Topology"
          />
          <NavButton
            active={activeTab === 'faceplate'}
            onClick={() => setActiveTab('faceplate')}
            icon={<LayoutGrid className="w-4 h-4" />}
            label="Faceplate"
          />
          
          <div className="w-px h-6 bg-border mx-2 self-center" />
          
          <IconButton onClick={toggleTheme} icon={theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />} />
          <IconButton onClick={() => setIsEventsOpen(true)} icon={<Bell className="w-4 h-4" />} badge={status?.unread_events} />
          <IconButton onClick={handleLogout} icon={<LogOut className="w-4 h-4" />} />
          <IconButton onClick={() => setIsSettingsOpen(true)} icon={<Settings className="w-4 h-4" />} />
        </nav>
      </header>

      {/* Toolbar */}
      <div className="flex items-center gap-3 px-5 py-2 bg-surface2 border-b border-border flex-shrink-0 z-30">
        <button 
          onClick={async () => { await apiFetch('/api/poll', { method: 'POST' }); fetchStatus(); }}
          className="flex items-center gap-2 px-3.5 py-1.5 bg-accent text-white rounded-md text-xs font-bold uppercase tracking-wide hover:opacity-90 transition-opacity shadow-lg shadow-accent/20"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Poll All Now
        </button>
        <button 
          onClick={() => { setRefreshKey(k => k + 1); fetchStatus(); fetchTopology(); }}
          className="px-3.5 py-1.5 bg-surface border border-border text-text2 rounded-md text-xs font-bold uppercase tracking-wide hover:text-white hover:border-accent transition-all"
        >
          Refresh
        </button>
        
        <div className="flex-1" />
        
        <div className="relative w-72 group">
          <Search className={`absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 transition-colors ${globalSearch ? 'text-accent' : 'text-text2 group-focus-within:text-accent'}`} />
          <input 
            type="text"
            className="w-full bg-bg border border-border rounded-lg pl-9 pr-4 py-1.5 text-xs text-white focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-all placeholder:text-text2/30"
            placeholder="Live search MAC, IP, or Host..."
            value={globalSearch}
            onChange={(e) => handleSearchChange(e.target.value)}
          />
          {globalSearch && (
            <button 
              onClick={() => setGlobalSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text2 hover:text-white transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {/* Main Content Area */}
      <main className="flex-1 overflow-hidden relative bg-bg">
        {activeTab === 'macnames' && <MacNamesView apiFetch={apiFetch} prefillMac={labelPrefill} onPrefillConsumed={() => setLabelPrefill(null)} onLabelsChanged={refreshLabels} onSearch={(q) => { setGlobalSearch(q); setActiveTab('search'); }} refreshKey={refreshKey} />}
        {activeTab === 'topology' && <TopologyView apiFetch={apiFetch} onJumpToFaceplate={handleJumpToFaceplate} />}
        {activeTab === 'faceplate' && <FaceplateView initialParams={jumpParams} onParamsConsumed={() => setJumpParams(null)} apiFetch={apiFetch} subnets={subnets} labels={labels} onLabelMac={handleLabelMac} refreshKey={refreshKey} onNavChange={handleFaceplateNavChange} />}
        {activeTab === 'search' && <SearchView onJumpToFaceplate={handleJumpToFaceplate} topologyData={topologyData} apiFetch={apiFetch} globalQuery={globalSearch} subnets={subnets} labels={labels} onLabelMac={handleLabelMac} />}
        {activeTab === 'dashboard' && <DashboardView apiFetch={apiFetch} onJumpToFaceplate={handleJumpToFaceplate} onSearch={(q) => { setGlobalSearch(q); setActiveTab('search'); }} subnets={subnets} refreshKey={refreshKey} />}
      </main>

      {/* Overlays */}
      <SidebarOverlay 
        isOpen={isEventsOpen} 
        onClose={() => setIsEventsOpen(false)} 
        title="Network Events & Alerts"
      >
        <EventsView apiFetch={apiFetch} />
      </SidebarOverlay>

      <SidebarOverlay 
        isOpen={isSettingsOpen} 
        onClose={() => setIsSettingsOpen(false)} 
        title="System Configuration"
        width="w-[550px]"
      >
        <SettingsView apiFetch={apiFetch} />
      </SidebarOverlay>
    </div>
  );
}

function NavButton({ active, onClick, icon, label }: any) {
  return (
    <button 
      onClick={onClick}
      className={`flex items-center gap-2 px-3.5 py-1.5 rounded-md text-sm transition-all ${
        active 
          ? 'bg-accent text-white font-medium shadow-sm' 
          : 'text-text2 hover:text-white hover:bg-surface2'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function IconButton({ onClick, icon, badge }: any) {
  return (
    <button
      onClick={onClick}
      className="relative p-2 text-text2 hover:text-white hover:bg-surface2 rounded-md transition-all"
    >
      {icon}
      {badge && (
        <span className="absolute top-1 right-1 flex items-center justify-center min-w-[14px] h-[14px] bg-red text-white text-[9px] font-bold rounded-full px-1">
          {badge}
        </span>
      )}
    </button>
  );
}
