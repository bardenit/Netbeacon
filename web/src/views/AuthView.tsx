import { useState, useEffect } from 'react';
import { Zap, Lock, Eye, EyeOff, ShieldCheck, RefreshCw } from 'lucide-react';

interface AuthViewProps {
  onLogin: (token: string) => void;
}

export default function AuthView({ onLogin }: AuthViewProps) {
  const [isSetup, setIsSetup] = useState<boolean | null>(null);
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    checkStatus();
  }, []);

  const checkStatus = async () => {
    try {
      const res = await fetch('/api/auth/status');
      const data = await res.json();
      setIsSetup(data.setup_required);
    } catch (e) {
      console.error("Auth status check failed", e);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    const endpoint = isSetup ? '/api/auth/setup' : '/api/auth/login';
    
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Authentication failed');
      
      onLogin(data.access_token);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  if (isSetup === null) return (
    <div className="h-screen w-screen bg-bg flex items-center justify-center">
      <Zap className="w-8 h-8 text-accent animate-pulse" />
    </div>
  );

  return (
    <div className="h-screen w-screen bg-bg flex items-center justify-center p-6 text-text">
      <div className="w-full max-w-md bg-surface border border-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-300">
        <div className="p-8 pb-4 text-center">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-accent/10 rounded-2xl mb-6">
            <Zap className="w-8 h-8 text-accent" fill="currentColor" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">
            {isSetup ? 'Set System Access Key' : 'NetBeacon Locked'}
          </h1>
          <p className="text-sm text-text2 mt-2">
            {isSetup 
              ? 'Enter a secure key to protect your network dashboard.' 
              : 'Enter the system access key to continue.'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="p-8 space-y-6">
          {error && (
            <div className="bg-red/10 border border-red/20 text-red text-xs p-3 rounded-lg text-center animate-in shake-in duration-300">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-[11px] font-bold text-text2 uppercase tracking-widest px-1">
                {isSetup ? 'New Access Key' : 'Access Key'}
              </label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text2" />
                <input 
                  autoFocus
                  required
                  type={showPassword ? "text" : "password"}
                  className="w-full bg-bg border border-border rounded-xl pl-11 pr-12 py-2.5 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-all text-white placeholder:text-text2/30"
                  placeholder={isSetup ? "Choose a strong key..." : "••••••••"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button 
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 p-1 text-text2 hover:text-white transition-colors"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </div>

          <button 
            type="submit"
            disabled={isLoading}
            className="w-full bg-accent hover:bg-accent/90 text-white py-3 rounded-xl font-bold text-sm shadow-lg shadow-accent/20 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <>
                {isSetup ? 'Initialize System' : 'Unlock Dashboard'}
                <ShieldCheck className="w-4 h-4 opacity-70" />
              </>
            )}
          </button>
        </form>

        <div className="p-6 bg-surface2 border-t border-border text-center">
          <p className="text-[10px] text-text2 uppercase tracking-widest font-medium">
            Protected by NetBeacon Security Engine
          </p>
        </div>
      </div>
    </div>
  );
}
