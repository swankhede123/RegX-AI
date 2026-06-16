import React, { createContext, useContext, useReducer, useCallback } from 'react';

const TaskContext = createContext(null);

const MAX_TASKS = 50;

function taskReducer(state, action) {
  switch (action.type) {
    case 'ADD': {
      const next = [action.task, ...state];
      return next.length > MAX_TASKS ? next.slice(0, MAX_TASKS) : next;
    }
    case 'UPDATE':
      return state.map(t => {
        if (t.id !== action.id) return t;
        const updates = { ...action.updates };
        if (updates.status === 'success' || updates.status === 'error') {
          updates.finishedAt = new Date();
        }
        return { ...t, ...updates };
      });
    case 'CLEAR_COMPLETED':
      return state.filter(t => t.status === 'running');
    default:
      return state;
  }
}

let taskSeq = 0;

export function TaskProvider({ children }) {
  const [tasks, dispatch] = useReducer(taskReducer, []);

  const addTask = useCallback((partial) => {
    const id = partial.id || `task-${Date.now()}-${++taskSeq}`;
    const task = {
      id,
      label: partial.label || 'Task',
      page: partial.page || '',
      status: 'running',
      progress: partial.progress ?? null,
      detail: partial.detail || '',
      startedAt: new Date(),
      finishedAt: null,
    };
    dispatch({ type: 'ADD', task });
    return id;
  }, []);

  const updateTask = useCallback((id, updates) => {
    dispatch({ type: 'UPDATE', id, updates });
  }, []);

  const clearCompleted = useCallback(() => {
    dispatch({ type: 'CLEAR_COMPLETED' });
  }, []);

  return (
    <TaskContext.Provider value={{ tasks, addTask, updateTask, clearCompleted }}>
      {children}
    </TaskContext.Provider>
  );
}

export function useTaskContext() {
  const ctx = useContext(TaskContext);
  if (!ctx) throw new Error('useTaskContext must be used within TaskProvider');
  return ctx;
}
