import React from 'react';
import { X } from 'lucide-react';

interface SidebarOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: string;
}

export default function SidebarOverlay({ isOpen, onClose, title, children, width = 'w-[450px]' }: SidebarOverlayProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/40 backdrop-blur-sm animate-in fade-in duration-200" 
        onClick={onClose}
      />
      
      {/* Panel */}
      <div className={`relative h-full ${width} bg-surface border-l border-border shadow-2xl flex flex-col animate-in slide-in-from-right duration-300`}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-surface2">
          <h2 className="text-lg font-bold text-white tracking-tight">{title}</h2>
          <button 
            onClick={onClose}
            className="p-2 text-text2 hover:text-white hover:bg-bg rounded-lg transition-all"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto">
          {children}
        </div>
      </div>
    </div>
  );
}
