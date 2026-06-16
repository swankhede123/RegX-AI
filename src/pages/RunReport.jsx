import React, { useState } from 'react';
import api from '../api';
import { API_BASE_URL } from '../config';
import { useTaskContext } from '../context/TaskContext';
import './RunReport.css';

const API_BASE = `${API_BASE_URL}/mcp/regression`;

export default function RunReport() {
  const { addTask, updateTask: updateTaskCtx } = useTaskContext();
  const [runFolder, setRunFolder] = useState('');
  const [loading, setLoading] = useState(false);
  const [qiData, setQiData] = useState(null);
  const [error, setError] = useState(null);
  const [sendingEmail, setSendingEmail] = useState(false);
  const [showEmailPreview, setShowEmailPreview] = useState(false);
  const [emailPreview, setEmailPreview] = useState(null);
  const [availableAnalysisFiles, setAvailableAnalysisFiles] = useState([]);
  const [selectedAnalysisFile, setSelectedAnalysisFile] = useState('');
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [useExistingFile, setUseExistingFile] = useState(false);

  const handleFetchAnalysisFiles = async () => {
    if (!runFolder.trim()) {
      alert('Please enter a run folder path first');
      return;
    }

    setLoadingFiles(true);
    setAvailableAnalysisFiles([]);
    setSelectedAnalysisFile('');

    try {
      const response = await api.post(`${API_BASE}/run-report/list-analysis-files`, {
        folder_path: runFolder.trim()
      });

      if (response.data.success) {
        setAvailableAnalysisFiles(response.data.files || []);
        if (response.data.files && response.data.files.length > 0) {
          setUseExistingFile(true);
        } else {
          alert('No analysis files found matching pattern analysis_*.xlsx');
        }
      } else {
        alert(response.data.error || 'Failed to fetch analysis files');
      }
    } catch (err) {
      console.error('Error fetching analysis files:', err);
      alert(err.response?.data?.error || err.message || 'Failed to fetch analysis files');
    } finally {
      setLoadingFiles(false);
    }
  };

  const handleAnalyze = async () => {
    if (!runFolder.trim()) {
      alert('Please enter a run folder path');
      return;
    }

    setLoading(true);
    setError(null);
    setQiData(null);
    const taskId = addTask({ label: `QI Analysis: ${runFolder.split('/').pop()}`, page: 'Run Report' });

    try {
      const payload = {
        run_folder: runFolder.trim()
      };

      if (useExistingFile && selectedAnalysisFile) {
        payload.analysis_file = selectedAnalysisFile;
      }

      const response = await api.post(`${API_BASE}/run-report/qi-analysis`, payload);

      if (response.data.success) {
        setQiData(response.data);
        updateTaskCtx(taskId, { status: 'success', detail: `Analysis complete: ${response.data.analysis_file}` });
      } else {
        const msg = response.data.error || 'Failed to analyze QI data';
        setError(msg);
        updateTaskCtx(taskId, { status: 'error', detail: msg });
      }
    } catch (err) {
      console.error('Error analyzing QI data:', err);
      const msg = err.response?.data?.error || err.message || 'Failed to analyze QI data';
      setError(msg);
      updateTaskCtx(taskId, { status: 'error', detail: msg });
    } finally {
      setLoading(false);
    }
  };

  const handlePreviewEmail = async () => {
    if (!qiData || !qiData.data.top_qi_impacting_bugs || qiData.data.top_qi_impacting_bugs.length === 0) {
      alert('No bugs available to send email');
      return;
    }

    // Filter bugs that impact more than 4 test cases
    const eligibleBugs = qiData.data.top_qi_impacting_bugs.filter(
      bug => (bug.impacted_tcs_latest_run || 0) > 4 && bug.overall_qi_impact !== undefined
    );

    if (eligibleBugs.length === 0) {
      alert('No bugs meet the criteria (impacting more than 4 test cases)');
      return;
    }

    // Extract branch name from run folder path
    const branchName = runFolder.split('/').pop() || 'Unknown Branch';

    try {
      const response = await api.post(`${API_BASE}/run-report/preview-email`, {
        bugs: eligibleBugs,
        branch_name: branchName,
        run_folder: runFolder
      });

      if (response.data.success) {
        setEmailPreview(response.data);
        setShowEmailPreview(true);
      } else {
        alert(`Failed to generate email preview: ${response.data.error || 'Unknown error'}`);
      }
    } catch (err) {
      console.error('Error generating email preview:', err);
      alert(`Failed to generate email preview: ${err.response?.data?.error || err.message || 'Unknown error'}`);
    }
  };

  const handleSendEmail = async () => {
    if (!emailPreview) {
      return;
    }

    setSendingEmail(true);
    const emailTaskId = addTask({ label: `Send QI email: ${emailPreview.branch_name}`, page: 'Run Report' });
    try {
      const response = await api.post(`${API_BASE}/run-report/send-email`, {
        bugs: emailPreview.bugs,
        branch_name: emailPreview.branch_name,
        run_folder: emailPreview.run_folder,
        recipients: emailPreview.recipients
      });

      if (response.data.success) {
        alert(`Email sent successfully to ${emailPreview.recipients.length} recipient(s)`);
        updateTaskCtx(emailTaskId, { status: 'success', detail: `Sent to ${emailPreview.recipients.length} recipient(s)` });
        setShowEmailPreview(false);
        setEmailPreview(null);
      } else {
        const msg = response.data.error || 'Unknown error';
        alert(`Failed to send email: ${msg}`);
        updateTaskCtx(emailTaskId, { status: 'error', detail: msg });
      }
    } catch (err) {
      console.error('Error sending email:', err);
      const msg = err.response?.data?.error || err.message || 'Unknown error';
      alert(`Failed to send email: ${msg}`);
      updateTaskCtx(emailTaskId, { status: 'error', detail: msg });
    } finally {
      setSendingEmail(false);
    }
  };

  return (
    <div className="run-report-container">
      <div className="run-report-header">
        <h1>📊 Run Report - QI Analysis</h1>
        <p>Upload run folder path to generate and analyze QI data from CSV files</p>
      </div>

      <div className="run-report-content">
        {/* Folder Path Input */}
        <div className="folder-input-section">
          <div className="form-group">
            <label>Run Folder Path</label>
            <input
              type="text"
              value={runFolder}
              onChange={(e) => {
                setRunFolder(e.target.value);
                // Reset existing file selection when folder changes
                setAvailableAnalysisFiles([]);
                setSelectedAnalysisFile('');
                setUseExistingFile(false);
              }}
              placeholder="e.g., /Users/sudharshan.musali/Downloads/Run4/QI"
              className="folder-input"
            />
            <small>
              Enter the full path to the folder containing:
              <ul>
                <li>tcms.csv</li>
                <li>regression_owners.csv</li>
                <li>jita.csv</li>
                <li>tcms_bugs.csv (or open-bugs.csv)</li>
              </ul>
              <p style={{ marginTop: '10px', fontWeight: 'bold' }}>The analysis Excel file will be generated automatically, or select an existing analysis file below.</p>
            </small>
          </div>
          
          <div style={{ display: 'flex', gap: '10px', marginTop: '10px', flexWrap: 'wrap' }}>
            <button 
              onClick={handleFetchAnalysisFiles} 
              disabled={loadingFiles || !runFolder.trim()}
              className="btn-secondary"
              style={{ background: '#6c757d', color: 'white' }}
            >
              {loadingFiles ? 'Loading...' : '📁 Fetch Existing Analysis Files'}
            </button>
            
            <button 
              onClick={handleAnalyze} 
              disabled={loading || !runFolder.trim()}
              className="btn-primary"
            >
              {loading ? 'Analyzing...' : useExistingFile && selectedAnalysisFile ? 'Analyze Selected File' : 'Analyze QI Data'}
            </button>
          </div>

          {/* Existing Analysis Files Selection */}
          {availableAnalysisFiles.length > 0 && (
            <div className="form-group" style={{ marginTop: '20px', padding: '15px', background: '#f8f9fa', borderRadius: '8px', border: '1px solid #ddd' }}>
              <label style={{ fontWeight: 'bold', marginBottom: '10px', display: 'block' }}>
                Select Existing Analysis File (analysis_*.xlsx):
              </label>
              <select
                value={selectedAnalysisFile}
                onChange={(e) => {
                  setSelectedAnalysisFile(e.target.value);
                  setUseExistingFile(e.target.value !== '');
                }}
                style={{
                  width: '100%',
                  padding: '8px',
                  fontSize: '14px',
                  border: '1px solid #ddd',
                  borderRadius: '4px',
                  marginBottom: '10px'
                }}
              >
                <option value="">-- Select an analysis file (or leave empty to generate new) --</option>
                {availableAnalysisFiles.map((file) => (
                  <option key={file} value={file}>
                    {file}
                  </option>
                ))}
              </select>
              <small style={{ color: '#666' }}>
                {selectedAnalysisFile 
                  ? `Selected: ${selectedAnalysisFile}. Click "Analyze Selected File" to use this existing file.`
                  : 'Leave unselected to generate a new analysis file from CSV files.'}
              </small>
            </div>
          )}
        </div>

        {/* Error Display */}
        {error && (
          <div className="error-message">
            <strong>Error:</strong> {error}
          </div>
        )}

        {/* QI Data Display */}
        {qiData && (
          <div className="qi-results">
            <div className="results-header">
              <h2>QI Analysis Results</h2>
              <p>Analysis File: <strong>{qiData.analysis_file}</strong></p>
            </div>

            {/* Overall Summary */}
            {qiData.data.summary && (
              <div className="summary-section">
                <h3>Overall Summary of the Run</h3>
                <div className="summary-content">
                  <pre>{qiData.data.summary}</pre>
                </div>
              </div>
            )}

            {/* Bug QI Summary */}
            {qiData.data.bug_qi_summary && qiData.data.bug_qi_summary.length > 0 && (
              <div className="bug-qi-summary-section">
                <h3>Bug QI Summary</h3>
                <p>Test, Product, Framework and Other: Testcase Count and Impacting QI</p>
                <div className="table-container">
                  <table className="qi-table">
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Priority</th>
                        <th>Testcase Count</th>
                        <th>Impacting QI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {qiData.data.bug_qi_summary.map((item, index) => (
                        <tr key={index}>
                          <td>{item.type || '-'}</td>
                          <td>{item.priority || '-'}</td>
                          <td>{item.testcase_count || 0}</td>
                          <td>{item.impacting_qi?.toFixed(2) || '0.00'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                
                {/* Total QI Impacted by Category */}
                {qiData.data.type_summary && (
                  <div className="type-summary-section">
                    <h4>Total QI Impacted by Category</h4>
                    <div className="type-summary-grid">
                      <div className="type-summary-item">
                        <span className="type-label">Test:</span>
                        <span className="type-value">{qiData.data.type_summary.test?.toFixed(2) || '0.00'}</span>
                      </div>
                      <div className="type-summary-item">
                        <span className="type-label">Product:</span>
                        <span className="type-value">{qiData.data.type_summary.product?.toFixed(2) || '0.00'}</span>
                      </div>
                      <div className="type-summary-item">
                        <span className="type-label">Framework:</span>
                        <span className="type-value">{qiData.data.type_summary.framework?.toFixed(2) || '0.00'}</span>
                      </div>
                      <div className="type-summary-item">
                        <span className="type-label">Other:</span>
                        <span className="type-value">{qiData.data.type_summary.other?.toFixed(2) || '0.00'}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Top QI Impacting Bugs */}
            {qiData.data.top_qi_impacting_bugs && qiData.data.top_qi_impacting_bugs.length > 0 && (
              <div className="top-bugs-section">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                  <div>
                    <h3>Top QI Impacting Bugs (Max 30)</h3>
                    <p>Sorted by Overall QI Impact (Ascending - Most Impactful First)</p>
                  </div>
                  <button
                    onClick={handlePreviewEmail}
                    className="btn-primary"
                    style={{ background: '#27ae60' }}
                  >
                    📧 Send Email to All Assignees
                  </button>
                </div>
                <div className="table-container">
                  <table className="qi-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Type</th>
                        <th>Priority</th>
                        <th>Summary</th>
                        <th>Assignee</th>
                        <th>Impacted TCs (Latest Run)</th>
                        <th>Deferred</th>
                        <th>Average QI</th>
                        <th>Overall QI Impact</th>
                      </tr>
                    </thead>
                    <tbody>
                      {qiData.data.top_qi_impacting_bugs.map((bug, index) => (
                        <tr key={index}>
                          <td>{bug.name || '-'}</td>
                          <td>{bug.type || '-'}</td>
                          <td>{bug.priority || '-'}</td>
                          <td className="summary-cell">{bug.summary || '-'}</td>
                          <td>{bug.assignee || '-'}</td>
                          <td>{bug.impacted_tcs_latest_run || 0}</td>
                          <td>{bug.deferred || '-'}</td>
                          <td>{bug.average_qi?.toFixed(2) || '0.00'}</td>
                          <td>{bug.overall_qi_impact?.toFixed(2) || '0.00'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {(!qiData.data.summary && 
              (!qiData.data.bug_qi_summary || qiData.data.bug_qi_summary.length === 0) && 
              (!qiData.data.top_qi_impacting_bugs || qiData.data.top_qi_impacting_bugs.length === 0)) && (
              <div className="no-data-message">
                <p>No QI analysis data found in the Excel file.</p>
              </div>
            )}
          </div>
        )}

        {/* Email Preview Modal */}
        {showEmailPreview && emailPreview && (
          <div className="email-preview-modal">
            <div className="email-preview-content">
              <div className="email-preview-header">
                <h2>Email Preview</h2>
                <button onClick={() => setShowEmailPreview(false)} className="close-btn">×</button>
              </div>
              
              <div className="email-preview-body">
                <div className="email-preview-section">
                  <strong>To:</strong> {emailPreview.recipients.join(', ')}
                </div>
                <div className="email-preview-section">
                  <strong>Subject:</strong> {emailPreview.subject}
                </div>
                <div className="email-preview-section">
                  <strong>Number of Bugs:</strong> {emailPreview.bugs.length}
                </div>
                
                <div className="email-preview-section">
                  <strong>Email Body Preview:</strong>
                  <div className="email-html-preview" dangerouslySetInnerHTML={{ __html: emailPreview.html_body }} />
                </div>
              </div>
              
              <div className="email-preview-footer">
                <button onClick={() => setShowEmailPreview(false)} className="btn-secondary">
                  Cancel
                </button>
                <button 
                  onClick={handleSendEmail} 
                  className="btn-primary"
                  disabled={sendingEmail}
                  style={{ background: '#27ae60' }}
                >
                  {sendingEmail ? 'Sending...' : 'Send Email'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
