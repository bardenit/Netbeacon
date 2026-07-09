import { useState, useEffect } from 'react';
import { Bell, Clock, Info, AlertTriangle, CheckCircle2, Trash2, CheckCircle, Zap, RefreshCw, Cpu, GitMerge, Thermometer, Radio, Copy, ArrowRight } from 'lucide-react';
import { formatIsoDate } from '../utils';

interface Event {
  id: number;
  device_id: number;
  event_type: string;
  detail: string;
  read: boolean;
  created_at: string;
  device_hostname?: string;
}

export default function EventsView({ apiFetch }: { apiFetch: (url: string, options?: any) => Promise<Response> }) {
  const [events, setEvents] = useState<Event[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    fetchEvents();
  }, []);

  const fetchEvents = async () => {
    setIsLoading(true);
    try {
      const res = await apiFetch('/api/events');
      const data = await res.json();
      setEvents(data);
    } catch (e) {
      console.error("Failed to fetch events", e);
    } finally {
      setIsLoading(false);
    }
  };

  const markAllRead = async () => {
    try {
      await apiFetch('/api/events/mark-read', { method: 'POST' });
      fetchEvents();
    } catch (e) {
      console.error("Failed to mark all read", e);
    }
  };

  const clearEvents = async () => {
    try {
      await apiFetch('/api/events', { method: 'DELETE' });
      setEvents([]);
    } catch (e) {
      console.error("Failed to clear events", e);
    }
  };

  return (
    <div className="flex flex-col h-full bg-bg/20">
      <div className="flex items-center justify-between px-6 py-3 border-b border-border bg-surface/50">
        <span className="text-[10px] font-bold text-text2 uppercase tracking-widest">
          {events.length} Recent Events
        </span>
        <div className="flex gap-2">
          <button 
            onClick={markAllRead}
            className="flex items-center gap-1.5 px-2 py-1 text-[10px] font-bold text-accent hover:bg-accent/10 rounded transition-all uppercase"
          >
            <CheckCircle className="w-3 h-3" />
            Mark all read
          </button>
          <button 
            onClick={clearEvents}
            className="flex items-center gap-1.5 px-2 py-1 text-[10px] font-bold text-red hover:bg-red/10 rounded transition-all uppercase"
          >
            <Trash2 className="w-3 h-3" />
            Clear all
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {events.map((event) => (
          <div 
            key={event.id}
            className={`p-4 rounded-xl border transition-all ${
              event.read ? 'bg-surface/40 border-border/50 opacity-70' : 'bg-surface border-border shadow-sm ring-1 ring-accent/5'
            }`}
          >
            <div className="flex gap-4">
              <div className={`mt-0.5 p-2 rounded-lg shrink-0 ${getEventColor(event.event_type)}`}>
                {getEventIcon(event.event_type)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-bold text-white truncate pr-2">
                    {event.device_hostname || 'System'}
                  </span>
                  <div className="flex items-center gap-1 text-[10px] text-text2 font-medium shrink-0">
                    <Clock className="w-3 h-3" />
                    {formatIsoDate(event.created_at, 'time')}
                  </div>
                </div>
                <p className="text-xs text-text2 leading-relaxed">
                  {event.detail}
                </p>
                <div className="mt-2 text-[10px] font-mono text-accent/60 uppercase tracking-tighter">
                  {event.event_type.replace(/_/g, ' ')}
                </div>
              </div>
            </div>
          </div>
        ))}

        {!isLoading && events.length === 0 && (
          <div className="h-40 flex flex-col items-center justify-center text-text2 italic border border-dashed border-border rounded-2xl m-2">
            <CheckCircle2 className="w-8 h-8 mb-2 opacity-20" />
            <p className="text-sm font-medium">No new alerts</p>
          </div>
        )}
      </div>
    </div>
  );
}

const EVENT_TYPE_ICONS: Record<string, JSX.Element> = {
  port_errors: <AlertTriangle className="w-4 h-4" />,
  poe_dropped: <Zap className="w-4 h-4" />,
  device_rebooted: <RefreshCw className="w-4 h-4" />,
  fan_failure: <AlertTriangle className="w-4 h-4" />,
  fan_recovered: <CheckCircle2 className="w-4 h-4" />,
  psu_failure: <AlertTriangle className="w-4 h-4" />,
  psu_recovered: <CheckCircle2 className="w-4 h-4" />,
  high_cpu: <Cpu className="w-4 h-4" />,
  stp_topology_change: <GitMerge className="w-4 h-4" />,
  slow_snmp: <Clock className="w-4 h-4" />,
  temp_rising: <Thermometer className="w-4 h-4" />,
  broadcast_storm: <Radio className="w-4 h-4" />,
  mac_duplicate: <Copy className="w-4 h-4" />,
  mac_moved: <ArrowRight className="w-4 h-4" />,
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  port_errors: 'bg-orange-500/10 text-orange-400',
  poe_dropped: 'bg-orange-500/10 text-orange-400',
  device_rebooted: 'bg-red/10 text-red',
  fan_failure: 'bg-red/10 text-red',
  fan_recovered: 'bg-green/10 text-green',
  psu_failure: 'bg-red/10 text-red',
  psu_recovered: 'bg-green/10 text-green',
  high_cpu: 'bg-red/10 text-red',
  stp_topology_change: 'bg-yellow/10 text-yellow',
  slow_snmp: 'bg-yellow/10 text-yellow',
  temp_rising: 'bg-red/10 text-red',
  broadcast_storm: 'bg-red/10 text-red',
  mac_duplicate: 'bg-red/10 text-red',
  mac_moved: 'bg-blue/10 text-blue',
};

function getEventIcon(type: string) {
  if (EVENT_TYPE_ICONS[type]) return EVENT_TYPE_ICONS[type];
  if (type.includes('down') || type.includes('error')) return <AlertTriangle className="w-4 h-4" />;
  if (type.includes('up') || type.includes('appeared')) return <CheckCircle2 className="w-4 h-4" />;
  return <Info className="w-4 h-4" />;
}

function getEventColor(type: string) {
  if (EVENT_TYPE_COLORS[type]) return EVENT_TYPE_COLORS[type];
  if (type.includes('down') || type.includes('error')) return 'bg-red/10 text-red';
  if (type.includes('up') || type.includes('appeared')) return 'bg-green/10 text-green';
  return 'bg-blue/10 text-blue';
}
