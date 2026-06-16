import React, { useState, useEffect, useCallback, useRef } from 'react';
import api from '../api';
import { API_BASE_URL } from '../config';
import './FailedTestcaseAnalysis.css';

const API_BASE = `${API_BASE_URL}/mcp/regression/failed-analysis`;
const JIRA_URL = 'https://jira.nutanix.com/browse/';

const COLUMNS = [
  { id: 'testcase_name', label: 'Testcase Name', defaultVisible: true },
  { id: 'regression_owner', label: 'Regression Owner', defaultVisible: true },
  { id: 'status', label: 'Status', defaultVisible: true },
  { id: 'failure_stage', label: 'Failure Stage', defaultVisible: true },
  { id: 'exception_summary', label: 'Exception Summary', defaultVisible: true },
  { id: 'ai_summary', label: 'AI Summary', defaultVisible: false },
  { id: 'triage_genie_ticket', label: 'Triage Genie Ticket', defaultVisible: true },
  { id: 'jira_tickets', label: 'Jira Tickets', defaultVisible: true },
  { id: 'comment', label: 'Comment', defaultVisible: true },
  { id: 'update_jita', label: 'Update Jita', defaultVisible: true },
  { id: 'issue_type', label: 'Issue Type', defaultVisible: false },
  { id: 'suggestion_by_ai_agent', label: 'Suggestion By AI Agent', defaultVisible: false },
  { id: 'intermittent', label: 'Intermittent', defaultVisible: false },
  { id: 'history_same_branch', label: 'History (Same Branch)', defaultVisible: false },
  { id: 'history_other_branch', label: 'History (Other Branch)', defaultVisible: false },
  { id: 'actions', label: 'Actions', defaultVisible: true },
];

const DEFAULT_VISIBLE = COLUMNS.filter(c => c.defaultVisible).map(c => c.id);
const STORAGE_KEY = 'failedAnalysisVisibleColumns';

function getStoredVisibleColumns() {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s) {
      const parsed = JSON.parse(s);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch (_) {}
  return DEFAULT_VISIBLE;
}

function getIntermittentLabel(r) {
  if (r.intermittent_rerun === 'Yes') return 'Yes';
  if (r.intermittent_rerun === 'No') return 'No';
  return '-';
}

export default function FailedTestcaseAnalysis() {
  const [loading, setLoading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [tag, setTag] = useState(() => localStorage.getItem('regressionDashboardTag') || 'cdp_master_full_reg');
  const [taskIds, setTaskIds] = useState('');
  const [inputMode, setInputMode] = useState('tag');
  const [results, setResults] = useState([]);
  const [filteredResults, setFilteredResults] = useState([]);
  const [error, setError] = useState(null);
  const [visibleColumns, setVisibleColumns] = useState(getStoredVisibleColumns);
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [columnCheckboxes, setColumnCheckboxes] = useState(() => {
    const vis = getStoredVisibleColumns();
    return COLUMNS.reduce((acc, c) => ({ ...acc, [c.id]: vis.includes(c.id) }), {});
  });
  const [currentBranch, setCurrentBranch] = useState('');
  const [analysisTag, setAnalysisTag] = useState('');
  const [commentEdits, setCommentEdits] = useState({});
  const [jiraAdd, setJiraAdd] = useState({});
  const [updateLoading, setUpdateLoading] = useState({});
  const [historyCache, setHistoryCache] = useState({});
  const [filterOwner, setFilterOwner] = useState('');
  const [filterFailureStage, setFilterFailureStage] = useState('');
  const [filterIntermittent, setFilterIntermittent] = useState('');
  const [filterComment, setFilterComment] = useState('');
  const [selectedRows, setSelectedRows] = useState([]);
  const [bulkJiraTicket, setBulkJiraTicket] = useState('');
  const [bulkComment, setBulkComment] = useState('');
  const [bulkUpdating, setBulkUpdating] = useState(false);
  const selectAllCheckboxRef = useRef(null);

  const buildIncludeParam = useCallback((cols) => {
    const include = new Set(['basic', 'exception_summary', 'intermittent']);
    if (cols.includes('issue_type')) include.add('issue_type');
    if (cols.includes('suggestion_by_ai_agent')) include.add('suggestion');
    if (cols.includes('triage_genie_ticket')) include.add('triage_genie_ticket');
    if (cols.includes('ai_summary')) include.add('ai_summary');
    return Array.from(include).join(',');
  }, []);

  const handleAnalyze = async () => {
    if (!tag.trim() && !taskIds.trim()) {
      alert('Please provide either a tag or task IDs');
      return;
    }
    setAnalyzing(true);
    setError(null);
    setResults([]);
    setFilteredResults([]);
    setFilterOwner('');
    setFilterFailureStage('');
    setFilterIntermittent('');
    setFilterComment('');
    setSelectedRows([]);
    setHistoryCache({});
    const include = buildIncludeParam(visibleColumns);
    const searchParams = new URLSearchParams({ include });
    if (inputMode === 'tag' && tag.trim()) searchParams.set('tag', tag.trim());
    else if (inputMode === 'task_ids' && taskIds.trim()) searchParams.set('task_ids', taskIds.trim());
    const url = `${API_BASE}/analyze-stream?${searchParams.toString()}`;

    try {
      const token = localStorage.getItem('regx_auth_token');
      const response = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        setError(errData.error || `Request failed: ${response.status}`);
        setAnalyzing(false);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'start') {
                setCurrentBranch(event.current_branch || '');
                setAnalysisTag(event.tag ?? '');
                if (event.total === 0) {
                  setAnalyzing(false);
                  alert('No failed testcases found for the given criteria.');
                }
              } else if (event.type === 'row' && event.result) {
                setResults(prev => [...prev, event.result]);
              } else if (event.type === 'done') {
                setAnalyzing(false);
              } else if (event.type === 'error') {
                setError(event.message || 'Analysis failed');
                setAnalyzing(false);
              }
            } catch (e) {
              // skip malformed lines
            }
          }
        }
      }
      if (buffer.trim()) {
        try {
          const line = buffer.trim();
          if (line.startsWith('data: ')) {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'row' && event.result) {
              setResults(prev => [...prev, event.result]);
            } else if (event.type === 'done') setAnalyzing(false);
            else if (event.type === 'error') {
              setError(event.message || 'Analysis failed');
              setAnalyzing(false);
            }
          }
        } catch (_) {}
      }
      setAnalyzing(false);
    } catch (err) {
      console.error('Error analyzing testcases:', err);
      const errorMessage = err.message || 'Failed to analyze testcases';
      if (errorMessage.includes('Failed to fetch') || errorMessage.includes('NetworkError')) {
        setError(`Network Error: ${errorMessage}. Please check your network connection and ensure you can access the server.`);
      } else {
        setError(errorMessage);
      }
      setAnalyzing(false);
    }
  };

  const getIssueTypeBadge = (issueType) => {
    if (issueType === 'Test Issue') return <span className="badge badge-test-issue">Test Issue</span>;
    if (issueType === 'Product Issue') return <span className="badge badge-product-issue">Product Issue</span>;
    if (issueType === 'Unknown / Needs Manual Review') return <span className="badge badge-unknown-issue">Unknown / Needs Review</span>;
    return <span className="badge badge-unknown">-</span>;
  };

  const getFailureStageBadge = (stage) => {
    const stageColors = { 'Test Body': 'stage-test-body', 'Test Setup': 'stage-setup', 'Teardown': 'stage-teardown', 'Infra': 'stage-infra' };
    return <span className={`badge ${stageColors[stage] || 'stage-unknown'}`}>{stage || 'Unknown'}</span>;
  };

  const handleCustomizeDone = () => {
    const selected = COLUMNS.filter(c => columnCheckboxes[c.id]).map(c => c.id);
    if (selected.length === 0) return;
    setVisibleColumns(selected);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(selected));
    } catch (_) {}
    setCustomizeOpen(false);
  };

  const openCustomize = () => {
    setColumnCheckboxes(COLUMNS.reduce((acc, c) => ({ ...acc, [c.id]: visibleColumns.includes(c.id) }), {}));
    setCustomizeOpen(true);
  };

  useEffect(() => {
    let filtered = [...results];
    if (filterOwner) filtered = filtered.filter(r => r.regression_owner && r.regression_owner.toLowerCase().includes(filterOwner.toLowerCase()));
    if (filterFailureStage) filtered = filtered.filter(r => r.failure_stage === filterFailureStage);
    if (filterIntermittent) filtered = filtered.filter(r => getIntermittentLabel(r) === filterIntermittent);
    if (filterComment.trim()) {
      const q = filterComment.trim().toLowerCase();
      filtered = filtered.filter(r => {
        const id = r.testcase_id;
        const text = id != null && commentEdits[id] !== undefined
          ? String(commentEdits[id])
          : String(r.comments || '');
        return text.toLowerCase().includes(q);
      });
    }
    setFilteredResults(filtered);
  }, [results, filterOwner, filterFailureStage, filterIntermittent, filterComment, commentEdits]);

  const uniqueOwners = [...new Set(results.map(r => r.regression_owner).filter(Boolean))].sort();
  const uniqueFailureStages = [...new Set(results.map(r => r.failure_stage).filter(Boolean))].sort();
  const uniqueIntermittent = [...new Set(results.map(r => getIntermittentLabel(r)))].sort((a, b) => {
    const order = { Yes: 0, No: 1, '-': 2 };
    return (order[a] ?? 99) - (order[b] ?? 99);
  });

  const fetchHistory = useCallback(async (testName, sameBranch) => {
    if (!analysisTag || testName == null) return null;
    const key = `${testName}|${sameBranch}`;
    if (historyCache[key]) return historyCache[key];
    try {
      const { data } = await api.get(`${API_BASE}/history`, {
        params: { test_name: testName, branch: currentBranch, same_branch: sameBranch, tag: analysisTag }
      });
      const runs = data.runs || [];
      setHistoryCache(prev => ({ ...prev, [key]: runs }));
      return runs;
    } catch (_) {
      return [];
    }
  }, [analysisTag, currentBranch, historyCache]);

  const applyTriageUpdate = async (testId, comment, jiraTicket) => {
    const { data } = await api.put(`${API_BASE}/update-triage`, {
      test_id: testId,
      comment: comment || '',
      jira_ticket: jiraTicket || undefined
    });
    return !!data.success;
  };

  const handleUpdateJita = async (result) => {
    const testId = result.testcase_id;
    if (!testId) return;
    const comment = commentEdits[testId] !== undefined ? commentEdits[testId] : (result.comments || '');
    const tickets = result.jira_tickets || [];
    const added = jiraAdd[testId];
    const jiraTicket = (added && added.trim()) ? added.trim() : (tickets[0] || null);
    setUpdateLoading(prev => ({ ...prev, [testId]: true }));
    try {
      const success = await applyTriageUpdate(testId, comment, jiraTicket);
      if (success) {
        setResults(prev => prev.map(r => r.testcase_id === testId
          ? { ...r, comments: comment || r.comments, jira_tickets: jiraTicket ? [jiraTicket] : (r.jira_tickets || []) }
          : r));
        setCommentEdits(prev => { const n = { ...prev }; delete n[testId]; return n; });
        setJiraAdd(prev => { const n = { ...prev }; delete n[testId]; return n; });
      }
    } catch (err) {
      console.error('Update Jita failed:', err);
      alert(err.response?.data?.error || 'Failed to update Jita');
    } finally {
      setUpdateLoading(prev => ({ ...prev, [testId]: false }));
    }
  };

  const toggleRowSelect = (testId) => {
    if (!testId) return;
    setSelectedRows(prev => (prev.includes(testId) ? prev.filter(id => id !== testId) : [...prev, testId]));
  };

  const toggleSelectAllVisible = () => {
    const visibleIds = filteredResults.map(r => r.testcase_id).filter(Boolean);
    if (visibleIds.length === 0) return;
    const allSelected = visibleIds.every(id => selectedRows.includes(id));
    if (allSelected) {
      setSelectedRows(prev => prev.filter(id => !visibleIds.includes(id)));
    } else {
      setSelectedRows(prev => [...new Set([...prev, ...visibleIds])]);
    }
  };

  useEffect(() => {
    const el = selectAllCheckboxRef.current;
    if (!el) return;
    const visibleIds = filteredResults.map(r => r.testcase_id).filter(Boolean);
    const count = visibleIds.filter(id => selectedRows.includes(id)).length;
    el.indeterminate = count > 0 && count < visibleIds.length;
  }, [filteredResults, selectedRows]);

  const handleBulkUpdate = async () => {
    const ids = selectedRows.filter(Boolean);
    if (ids.length === 0) {
      alert('Select at least one row.');
      return;
    }
    const jira = bulkJiraTicket.trim();
    const comment = bulkComment || '';
    setBulkUpdating(true);
    let ok = 0;
    let fail = 0;
    try {
      for (const testId of ids) {
        try {
          const success = await applyTriageUpdate(testId, comment, jira || undefined);
          if (success) {
            ok++;
            setResults(prev => prev.map(r => {
              if (r.testcase_id !== testId) return r;
              return {
                ...r,
                comments: comment || r.comments,
                jira_tickets: jira ? [jira] : (r.jira_tickets || [])
              };
            }));
            setCommentEdits(prev => { const n = { ...prev }; delete n[testId]; return n; });
            setJiraAdd(prev => { const n = { ...prev }; delete n[testId]; return n; });
          } else {
            fail++;
          }
        } catch {
          fail++;
        }
      }
      if (fail > 0) {
        alert(`Bulk update finished: ${ok} succeeded, ${fail} failed.`);
      } else if (ok > 0) {
        alert(`Updated ${ok} testcase(s).`);
      }
    } finally {
      setBulkUpdating(false);
    }
  };

  const renderHistoryCell = (result, sameBranch) => {
    if (!analysisTag) return <span className="history-unknown">—</span>;
    const testName = result.testcase_name;
    const key = `${testName}|${sameBranch}`;
    const cached = historyCache[key];
    if (cached) {
      return (
        <div className="history-runs">
          {cached.slice(0, 3).map((run, i) => (
            <span key={i} className="history-run-item">
              {run.status === 'passed' ? (
                <span className="history-tick" title="Passed">✓</span>
              ) : run.status === 'failed' ? (
                run.jira_ticket ? (
                  <a href={`${JIRA_URL}${run.jira_ticket}`} target="_blank" rel="noopener noreferrer" className="jira-link">{run.jira_ticket}</a>
                ) : run.comment ? (
                  <span className="history-comment" title={run.comment}>💬</span>
                ) : (
                  <span className="history-cross" title="Failed">✗</span>
                )
              ) : (
                <span className="history-unknown">-</span>
              )}
            </span>
          ))}
        </div>
      );
    }
    return <span className="history-loading">Loading…</span>;
  };

  const renderCell = (colId, result, index) => {
    switch (colId) {
      case 'testcase_name':
        return <td key={colId} className="testcase-name" title={result.testcase_name}>{result.testcase_name || '-'}</td>;
      case 'regression_owner':
        return <td key={colId} className="owner-cell">{result.regression_owner || 'Unknown'}</td>;
      case 'status':
        return <td key={colId}><span className="badge badge-failed">FAILED</span></td>;
      case 'failure_stage':
        return <td key={colId}>{getFailureStageBadge(result.failure_stage)}</td>;
      case 'exception_summary':
        return <td key={colId} className="exception-summary-cell" title={result.exception_summary}>{result.exception_summary || '-'}</td>;
      case 'ai_summary':
        return <td key={colId} className="ai-summary-cell" title={result.ai_summary}>{result.ai_summary || '-'}</td>;
      case 'jira_tickets': {
        const tickets = result.jira_tickets || [];
        const added = jiraAdd[result.testcase_id];
        return (
          <td key={colId}>
            <div className="jira-tickets">
              {tickets.map((ticket, idx) => (
                <a key={idx} href={`${JIRA_URL}${ticket}`} target="_blank" rel="noopener noreferrer" className="jira-link">{ticket}</a>
              ))}
              {added && <a href={`${JIRA_URL}${added}`} target="_blank" rel="noopener noreferrer" className="jira-link">{(added.length > 12 ? added.slice(0, 12) + '…' : added)}</a>}
            </div>
            <input
              type="text"
              className="jira-add-input"
              placeholder="Add ticket"
              value={added || ''}
              onChange={e => setJiraAdd(prev => ({ ...prev, [result.testcase_id]: e.target.value }))}
            />
          </td>
        );
      }
      case 'comment':
        return (
          <td key={colId}>
            <input
              type="text"
              className="comment-input"
              value={commentEdits[result.testcase_id] !== undefined ? commentEdits[result.testcase_id] : (result.comments || '')}
              onChange={e => setCommentEdits(prev => ({ ...prev, [result.testcase_id]: e.target.value }))}
              placeholder="Comment"
            />
          </td>
        );
      case 'update_jita':
        return (
          <td key={colId}>
            <button
              type="button"
              className="btn-update-jita"
              disabled={updateLoading[result.testcase_id]}
              onClick={() => handleUpdateJita(result)}
            >
              {updateLoading[result.testcase_id] ? 'Updating…' : 'Update Jita'}
            </button>
          </td>
        );
      case 'issue_type':
        return <td key={colId}>{getIssueTypeBadge(result.issue_type)}</td>;
      case 'suggestion_by_ai_agent':
        return <td key={colId} className="suggestion-cell" title={result.suggestion_by_ai_agent}>{result.suggestion_by_ai_agent || '-'}</td>;
      case 'intermittent':
        return <td key={colId}>{getIntermittentLabel(result)}</td>;
      case 'history_same_branch':
        return <td key={colId} className="history-cell">{renderHistoryCell(result, true)}</td>;
      case 'history_other_branch':
        return <td key={colId} className="history-cell">{renderHistoryCell(result, false)}</td>;
      case 'triage_genie_ticket':
        return (
          <td key={colId}>
            {result.triage_genie_ticket_id ? (
              <a href={`${JIRA_URL}${result.triage_genie_ticket_id}`} target="_blank" rel="noopener noreferrer" className="jira-link triage-genie-ticket">{result.triage_genie_ticket_id}</a>
            ) : '-'}
          </td>
        );
      case 'actions':
        return (
          <td key={colId}>
            {result.test_log_url && (
              <a href={result.test_log_url} target="_blank" rel="noopener noreferrer" className="btn-link">View Log</a>
            )}
          </td>
        );
      default:
        return <td key={colId}>-</td>;
    }
  };

  useEffect(() => {
    const needSame = visibleColumns.includes('history_same_branch');
    const needOther = visibleColumns.includes('history_other_branch');
    if (!needSame && !needOther || !analysisTag || !currentBranch || filteredResults.length === 0) return;
    filteredResults.forEach((result, idx) => {
      const testName = result.testcase_name;
      if (!testName) return;
      const keySame = `${testName}|true`;
      const keyOther = `${testName}|false`;
      if (needSame && !historyCache[keySame]) {
        fetchHistory(testName, true);
      }
      if (needOther && !historyCache[keyOther]) {
        fetchHistory(testName, false);
      }
    });
  }, [visibleColumns, analysisTag, currentBranch, filteredResults.length, fetchHistory, historyCache]);

  const visibleIdsForHeader = filteredResults.map(r => r.testcase_id).filter(Boolean);
  const selectedVisibleCount = visibleIdsForHeader.filter(id => selectedRows.includes(id)).length;
  const allVisibleSelected = visibleIdsForHeader.length > 0 && selectedVisibleCount === visibleIdsForHeader.length;

  return (
    <div className="failed-analysis-container">
      <div className="failed-analysis-header">
        <h1>🔍 Failed Testcase Analysis - RegX-AI Agent</h1>
        <div className="header-actions">
          <button onClick={handleAnalyze} className="btn-primary" disabled={analyzing || loading}>
            {analyzing ? 'Analyzing...' : '🔍 Analyze Failed Testcases'}
          </button>
          <button type="button" className="btn-customize-columns" onClick={openCustomize}>
            Customize Columns
          </button>
        </div>
      </div>

      <div className="analysis-controls">
        <div className="input-mode-selector">
          <label><input type="radio" value="tag" checked={inputMode === 'tag'} onChange={e => setInputMode(e.target.value)} /> Tag</label>
          <label><input type="radio" value="task_ids" checked={inputMode === 'task_ids'} onChange={e => setInputMode(e.target.value)} /> Task IDs</label>
        </div>
        {inputMode === 'tag' ? (
          <div className="form-group">
            <label>Tag <span className="required">*</span></label>
            <input type="text" value={tag} onChange={e => setTag(e.target.value)} placeholder="e.g., cdp_master_full_reg" onKeyPress={e => e.key === 'Enter' && handleAnalyze()} />
          </div>
        ) : (
          <div className="form-group">
            <label>Task IDs (comma-separated) <span className="required">*</span></label>
            <textarea value={taskIds} onChange={e => setTaskIds(e.target.value)} placeholder="e.g., 697aeae32bc0c49968d713f7" rows={3} />
          </div>
        )}
      </div>

      {error && <div className="error-message"><strong>Error:</strong> {error}</div>}
      {analyzing && (
        <div className="loading">
          <div className="spinner" />
          <p>Analyzing failed testcases... This may take a few moments.</p>
        </div>
      )}

      {results.length > 0 && (
        <div className="results-container">
          <div className="results-header">
            <h2>Analysis Results ({filteredResults.length} of {results.length} failed testcases)</h2>
          </div>
          <div className="filter-controls">
            <div className="filter-group">
              <label>Filter by Regression Owner:</label>
              <select value={filterOwner} onChange={e => setFilterOwner(e.target.value)} className="filter-select">
                <option value="">All Owners</option>
                {uniqueOwners.map(owner => <option key={owner} value={owner}>{owner}</option>)}
              </select>
            </div>
            <div className="filter-group">
              <label>Filter by Failure Stage:</label>
              <select value={filterFailureStage} onChange={e => setFilterFailureStage(e.target.value)} className="filter-select">
                <option value="">All Stages</option>
                {uniqueFailureStages.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="filter-group">
              <label>Filter by Intermittent:</label>
              <select value={filterIntermittent} onChange={e => setFilterIntermittent(e.target.value)} className="filter-select">
                <option value="">All</option>
                {uniqueIntermittent.map(v => (
                  <option key={v} value={v}>{v === '-' ? 'Unknown' : v}</option>
                ))}
              </select>
            </div>
            <div className="filter-group filter-group-comment">
              <label htmlFor="filter-comment">Filter by Comment:</label>
              <input
                id="filter-comment"
                type="text"
                className="filter-input"
                value={filterComment}
                onChange={e => setFilterComment(e.target.value)}
              />
            </div>
            {(filterOwner || filterFailureStage || filterIntermittent || filterComment.trim()) && (
              <button onClick={() => { setFilterOwner(''); setFilterFailureStage(''); setFilterIntermittent(''); setFilterComment(''); }} className="btn-clear-filters">Clear Filters</button>
            )}
          </div>
          <div className="results-table-toolbar">
            <div className="bulk-update-panel">
              <div className="bulk-field">
                <label htmlFor="bulk-jira-ticket">Jira ticket</label>
                <input
                  id="bulk-jira-ticket"
                  type="text"
                  className="bulk-input"
                  placeholder="e.g. PROJ-1234"
                  value={bulkJiraTicket}
                  onChange={e => setBulkJiraTicket(e.target.value)}
                />
              </div>
              <div className="bulk-field bulk-field-comment">
                <textarea
                  id="bulk-comment"
                  className="bulk-textarea bulk-textarea-compact"
                  rows={1}
                  placeholder="Comment"
                  aria-label="Comment"
                  value={bulkComment}
                  onChange={e => setBulkComment(e.target.value)}
                />
              </div>
              <button
                type="button"
                className="btn-bulk-update"
                disabled={bulkUpdating || selectedRows.length === 0}
                onClick={handleBulkUpdate}
              >
                {bulkUpdating ? 'Updating…' : 'Bulk Update'}
              </button>
            </div>
          </div>
          <div className="results-table-wrapper">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th className="col-select" title="Select rows">
                    <input
                      ref={selectAllCheckboxRef}
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={toggleSelectAllVisible}
                      disabled={visibleIdsForHeader.length === 0}
                      aria-label="Select all visible rows"
                    />
                  </th>
                  {COLUMNS.filter(c => visibleColumns.includes(c.id)).map(c => (
                    <th key={c.id}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredResults.map((result, index) => (
                  <tr key={result.testcase_id || index}>
                    <td className="col-select">
                      <input
                        type="checkbox"
                        checked={!!result.testcase_id && selectedRows.includes(result.testcase_id)}
                        onChange={() => toggleRowSelect(result.testcase_id)}
                        disabled={!result.testcase_id}
                        aria-label={`Select row ${result.testcase_name || index + 1}`}
                      />
                    </td>
                    {COLUMNS.filter(c => visibleColumns.includes(c.id)).map(c => renderCell(c.id, result, index))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {customizeOpen && (
        <div className="modal-overlay" onClick={() => setCustomizeOpen(false)}>
          <div className="modal-content customize-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Customize Columns</h3>
              <button type="button" className="modal-close" onClick={() => setCustomizeOpen(false)}>×</button>
            </div>
            <div className="modal-body">
              {COLUMNS.map(c => (
                <label key={c.id} className="column-checkbox-label">
                  <input
                    type="checkbox"
                    checked={columnCheckboxes[c.id] !== false}
                    onChange={e => setColumnCheckboxes(prev => ({ ...prev, [c.id]: e.target.checked }))}
                  />
                  {c.label}
                </label>
              ))}
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-primary" onClick={handleCustomizeDone}>Done</button>
              <button type="button" className="btn-secondary" onClick={() => setCustomizeOpen(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {!analyzing && results.length === 0 && filteredResults.length === 0 && !error && (
        <div className="empty-state">
          <p>Enter a tag or task IDs and click "Analyze Failed Testcases" to get started.</p>
          <p className="empty-state-hint">The AI agent will analyze failures and provide triage options.</p>
        </div>
      )}
    </div>
  );
}
