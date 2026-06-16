import React, { useState, useRef, useEffect } from 'react';
import { useTaskContext } from '../context/TaskContext';
import './TaskStatusIcon.css';

const RADIUS = 18;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function timeAgo(date) {
  if (!date) return '';
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function StatusIcon({ status }) {
  if (status === 'running') {
    return (
      <svg className="task-row-spinner" width="16" height="16" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="6" fill="none" stroke="#3498db" strokeWidth="2"
          strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === 'success') {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="7" fill="#27ae60" />
        <path d="M5 8l2 2 4-4" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16">
      <circle cx="8" cy="8" r="7" fill="#e74c3c" />
      <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export default function TaskStatusIcon() {
  const { tasks, clearCompleted } = useTaskContext();
  const [open, setOpen] = useState(false);
  const popupRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (popupRef.current && !popupRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  if (tasks.length === 0) return null;

  const runningCount = tasks.filter(t => t.status === 'running').length;
  const hasError = tasks.some(t => t.status === 'error');
  const allDone = runningCount === 0;

  let ringColor = '#3498db';
  if (allDone && hasError) ringColor = '#e74c3c';
  else if (allDone) ringColor = '#27ae60';

  const progress = allDone ? 1 : (tasks.length - runningCount) / tasks.length;
  const dashoffset = CIRCUMFERENCE * (1 - progress);

  return (
    <div className="task-status-wrapper" ref={popupRef}>
      {open && (
        <div className="task-popup">
          <div className="task-popup-header">
            <span className="task-popup-title">Tasks</span>
            <button className="task-popup-clear" onClick={clearCompleted}>
              Clear completed
            </button>
          </div>
          <div className="task-popup-list">
            {tasks.map(t => (
              <div key={t.id} className={`task-popup-row task-row-${t.status}`}>
                <StatusIcon status={t.status} />
                <div className="task-row-body">
                  <div className="task-row-label">{t.label}</div>
                  <div className="task-row-meta">
                    {t.page && <span className="task-row-page">{t.page}</span>}
                    <span className="task-row-time">{timeAgo(t.startedAt)}</span>
                  </div>
                  {t.detail && <div className="task-row-detail">{t.detail}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <button
        className={`task-status-btn ${runningCount > 0 ? 'has-running' : ''}`}
        onClick={() => setOpen(v => !v)}
        title={`${runningCount} running, ${tasks.length} total`}
      >
        <svg width="44" height="44" viewBox="0 0 44 44" className="task-ring-svg">
          <circle cx="22" cy="22" r={RADIUS} fill="none" stroke="#e0e0e0" strokeWidth="3" />
          <circle
            cx="22" cy="22" r={RADIUS}
            fill="none"
            stroke={ringColor}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={dashoffset}
            className={runningCount > 0 ? 'ring-animated' : ''}
            transform="rotate(-90 22 22)"
          />
        </svg>
        <span className="task-status-count">
          {runningCount > 0 ? runningCount : '\u2713'}
        </span>
      </button>
    </div>
  );
}
