import React from 'react';
import { X } from 'lucide-react';

interface SidebarOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: string;
}

export default function SidebarOverlay({ isOpen, onClose, title, children, width = 'w-[480px]' }: SidebarOverlayProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/50" onClick={onClose} />
      <div className={`${width} bg-surface border-l border-border flex flex-col h-full shadow-2xl`}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-border flex-shrink-0">
          <h2 className="font-semibold text-white text-sm">{title}</h2>
          <button onClick={onClose} className="p-1.5 text-text2 hover:text-white hover:bg-surface2 rounded-md transition-all">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {children}
        </div>
      </div>
    </div>
  );
}
