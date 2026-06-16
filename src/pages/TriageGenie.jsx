import React, { useState, useEffect } from 'react';
import api from '../api';
import { API_BASE_URL } from '../config';
import { useTaskContext } from '../context/TaskContext';
import './TriageGenie.css';

const API_BASE = `${API_BASE_URL}/mcp/regression/triage-genie`;

export default function TriageGenie() {
  const { addTask, updateTask: updateTaskCtx } = useTaskContext();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    jita_task_ids: '',
    skip_review: false,
    created_by: ''
  });

  useEffect(() => {
    fetchJobs();
    
    // Check for pre-filled data from Run Plan
    const prefillData = localStorage.getItem('triageGeniePrefill');
    if (prefillData) {
      try {
        const data = JSON.parse(prefillData);
        setFormData({
          name: data.name || '',
          jita_task_ids: data.jita_task_ids || '',
          skip_review: false,
          created_by: ''
        });
        setShowCreateModal(true);
        // Clear the prefill data after using it
        localStorage.removeItem('triageGeniePrefill');
      } catch (e) {
        console.error('Error parsing prefill data:', e);
      }
    }
    
    // Listen for navigation events from Run Plan
    const handleNavigate = (event) => {
      const data = event.detail;
      setFormData({
        name: data.name || '',
        jita_task_ids: data.jita_task_ids || '',
        skip_review: false,
        created_by: ''
      });
      setShowCreateModal(true);
    };
    
    window.addEventListener('navigateToTriageGenie', handleNavigate);
    
    return () => {
      window.removeEventListener('navigateToTriageGenie', handleNavigate);
    };
  }, []);

  const fetchJobs = async () => {
    setLoading(true);
    try {
      const response = await api.get(`${API_BASE}/jobs`, {
        params: {
          page: 1,
          per_page: 10,
          show_all: 'true'
        }
      });
      if (response.data.success) {
        setJobs(response.data.jobs || []);
      } else {
        const errorMsg = response.data.error || 'Failed to fetch jobs';
        alert(errorMsg);
        if (errorMsg.includes('authentication') || errorMsg.includes('TRIAGE_GENIE_TOKEN')) {
          console.error('Authentication error: Please set TRIAGE_GENIE_TOKEN environment variable');
        }
      }
    } catch (error) {
      console.error('Error fetching jobs:', error);
      const errorMsg = error.response?.data?.error || error.message;
      alert(`Failed to fetch jobs: ${errorMsg}`);
      if (errorMsg.includes('authentication') || errorMsg.includes('401')) {
        console.error('Authentication error: Please set TRIAGE_GENIE_TOKEN environment variable');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleCreateJob = async () => {
    if (!formData.name.trim()) {
      alert('Name is required');
      return;
    }
    if (!formData.jita_task_ids.trim()) {
      alert('JITA task IDs are required');
      return;
    }

    setLoading(true);
    const taskId = addTask({ label: `Create Triage Job: ${formData.name}`, page: 'Triage Genie' });
    try {
      const response = await api.post(`${API_BASE}/jobs`, {
        name: formData.name,
        jita_task_ids: formData.jita_task_ids,
        skip_review: formData.skip_review,
        created_by: formData.created_by || 'user'
      });

      if (response.data.success) {
        alert('Job created successfully!');
        updateTaskCtx(taskId, { status: 'success', detail: 'Job created' });
        setShowCreateModal(false);
        setFormData({
          name: '',
          jita_task_ids: '',
          skip_review: false,
          created_by: ''
        });
        fetchJobs();
      } else {
        const errorMsg = response.data.error || 'Unknown error';
        alert(`Failed to create job: ${errorMsg}`);
        updateTaskCtx(taskId, { status: 'error', detail: errorMsg });
        if (errorMsg.includes('authentication') || errorMsg.includes('TRIAGE_GENIE_TOKEN')) {
          console.error('Authentication error: Please set TRIAGE_GENIE_TOKEN environment variable');
        }
      }
    } catch (error) {
      console.error('Error creating job:', error);
      const errorMsg = error.response?.data?.error || error.message;
      alert(`Failed to create job: ${errorMsg}`);
      updateTaskCtx(taskId, { status: 'error', detail: errorMsg });
      if (errorMsg.includes('authentication') || errorMsg.includes('401')) {
        console.error('Authentication error: Please set TRIAGE_GENIE_TOKEN environment variable');
      }
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return '-';
    try {
      const date = new Date(dateString);
      return date.toLocaleString();
    } catch (e) {
      return dateString;
    }
  };

  const handleViewTriages = (jobId) => {
    const url = `http://triage-genie.eng.nutanix.com/tasks/${jobId}`;
    window.open(url, '_blank');
  };

  return (
    <div className="triage-genie-container">
      <div className="triage-genie-header">
        <h1>🤖 Triage Genie - Automated Failure Triage</h1>
        <div className="header-actions">
          <button onClick={fetchJobs} className="btn-secondary" disabled={loading}>
            {loading ? 'Refreshing...' : '🔄 Refresh'}
          </button>
          <button onClick={() => setShowCreateModal(true)} className="btn-primary">
            + Create New Job
          </button>
        </div>
      </div>

      {loading && jobs.length === 0 ? (
        <div className="loading">Loading jobs...</div>
      ) : (
        <div className="jobs-list">
          {jobs.length === 0 ? (
            <div className="empty-state">
              <p>No jobs found. Create a new job to get started.</p>
            </div>
          ) : (
            <table className="triage-genie-table">
              <thead>
                <tr>
                  <th>Job ID</th>
                  <th>Name</th>
                  <th>Created Time</th>
                  <th>JITA Tasks</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  // Build JITA URL with all task IDs
                  const getJitaUrl = () => {
                    let taskIds = [];
                    if (job.jita_task_id_list && job.jita_task_id_list.length > 0) {
                      taskIds = job.jita_task_id_list;
                    } else if (job.jita_task_ids) {
                      taskIds = job.jita_task_ids.split(',').map(id => id.trim()).filter(id => id);
                    }
                    if (taskIds.length === 0) return null;
                    return `https://jita.eng.nutanix.com/results?task_ids=${taskIds.join(',')}&active_tab=1&merge_tests=true`;
                  };
                  
                  const jitaUrl = getJitaUrl();
                  const taskDisplay = job.jita_task_id_list && job.jita_task_id_list.length > 0
                    ? job.jita_task_id_list.join(', ')
                    : job.jita_task_ids || '-';
                  
                  return (
                    <tr key={job.id}>
                      <td>{job.id}</td>
                      <td>{job.name || '-'}</td>
                      <td>{formatDate(job.create_time)}</td>
                      <td>
                        {jitaUrl ? (
                          <a 
                            href={jitaUrl} 
                            target="_blank" 
                            rel="noopener noreferrer"
                            className="jita-link"
                          >
                            JITA
                          </a>
                        ) : (
                          taskDisplay
                        )}
                      </td>
                      <td>
                        <button
                          onClick={() => handleViewTriages(job.id)}
                          className="btn-view-triages"
                        >
                          View Triages
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Create Job Modal */}
      {showCreateModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h2>Create New Triage Genie Job</h2>
              <button onClick={() => setShowCreateModal(false)} className="close-btn">×</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>Name <span className="required">*</span></label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="e.g., Sudharshan_RegX"
                />
              </div>
              <div className="form-group">
                <label>JITA Task IDs (comma separated) <span className="required">*</span></label>
                <textarea
                  value={formData.jita_task_ids}
                  onChange={(e) => setFormData({ ...formData, jita_task_ids: e.target.value })}
                  placeholder="e.g., 697aeae32bc0c49968d713f7, 697aeae32bc0c49968d713f8"
                  rows="3"
                />
                <small>Enter comma-separated JITA task IDs</small>
              </div>
              <div className="form-group">
                <label>
                  <input
                    type="checkbox"
                    checked={formData.skip_review}
                    onChange={(e) => setFormData({ ...formData, skip_review: e.target.checked })}
                  />
                  Skip Review
                </label>
              </div>
              <div className="form-group">
                <label>Created By</label>
                <input
                  type="text"
                  value={formData.created_by}
                  onChange={(e) => setFormData({ ...formData, created_by: e.target.value })}
                  placeholder="e.g., sudharshan.musali"
                />
                <small>Optional: Leave empty to use default</small>
              </div>
            </div>
            <div className="modal-footer">
              <button onClick={() => setShowCreateModal(false)} className="btn-secondary">
                Cancel
              </button>
              <button onClick={handleCreateJob} className="btn-primary" disabled={loading}>
                {loading ? 'Creating...' : 'Create Job'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
