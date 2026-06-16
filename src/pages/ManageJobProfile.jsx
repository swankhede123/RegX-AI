import React, { useState } from 'react';
import axios from 'axios';
import { API_BASE_URL } from '../config';
import './DynamicJobProfile.css';

const API_BASE = `${API_BASE_URL}/mcp/regression/dynamic-jp`;

export default function ManageJobProfile() {
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);

  const [jobProfiles, setJobProfiles] = useState([]);
  const [testSets, setTestSets] = useState([]);
  const [searched, setSearched] = useState(false);

  const [selectedJPs, setSelectedJPs] = useState(new Set());
  const [selectedTSs, setSelectedTSs] = useState(new Set());
  const [deleting, setDeleting] = useState(false);
  const [deleteResults, setDeleteResults] = useState(null);

  const getErrorMessage = (err) =>
    err?.response?.data?.error || err?.message || 'Unknown error';

  const handleSearch = async () => {
    const q = searchQuery.trim();
    if (q.length < 2) {
      setErrorMsg('Please enter at least 2 characters to search');
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    setDeleteResults(null);
    setSelectedJPs(new Set());
    setSelectedTSs(new Set());
    try {
      const resp = await axios.post(`${API_BASE}/search`, { query: q, limit: 30 });
      setJobProfiles(resp.data?.job_profiles || []);
      setTestSets(resp.data?.test_sets || []);
      setSearched(true);
      if (!resp.data?.job_profiles?.length && !resp.data?.test_sets?.length) {
        setErrorMsg(`No Job Profiles or Test Sets found matching "${q}"`);
      }
    } catch (err) {
      setErrorMsg(`Search failed: ${getErrorMessage(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const toggleJP = (id) => {
    setSelectedJPs((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleTS = (id) => {
    setSelectedTSs((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleDelete = async () => {
    const jpIds = [...selectedJPs];
    const tsIds = [...selectedTSs];
    if (jpIds.length === 0 && tsIds.length === 0) {
      setErrorMsg('Please select at least one Job Profile or Test Set to delete');
      return;
    }

    const count = jpIds.length + tsIds.length;
    const confirmMsg = `Are you sure you want to delete ${count} item(s)?\n\n` +
      (jpIds.length ? `Job Profiles: ${jpIds.length}\n` : '') +
      (tsIds.length ? `Test Sets: ${tsIds.length}\n` : '') +
      '\nThis action cannot be undone.';

    if (!window.confirm(confirmMsg)) return;

    setDeleting(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    setDeleteResults(null);
    try {
      const resp = await axios.post(`${API_BASE}/delete`, {
        jp_ids: jpIds,
        ts_ids: tsIds,
      });
      setDeleteResults(resp.data?.results || {});

      const jpOk = (resp.data?.results?.job_profiles || []).filter((r) => r.success).length;
      const tsOk = (resp.data?.results?.test_sets || []).filter((r) => r.success).length;
      const jpFail = jpIds.length - jpOk;
      const tsFail = tsIds.length - tsOk;

      if (jpFail === 0 && tsFail === 0) {
        setSuccessMsg(`Successfully deleted ${jpOk + tsOk} item(s)`);
      } else {
        setErrorMsg(`Deleted ${jpOk + tsOk} item(s), ${jpFail + tsFail} failed. See details below.`);
      }

      // Remove successfully deleted items from lists
      const deletedJPIds = new Set(
        (resp.data?.results?.job_profiles || []).filter((r) => r.success).map((r) => r._id)
      );
      const deletedTSIds = new Set(
        (resp.data?.results?.test_sets || []).filter((r) => r.success).map((r) => r._id)
      );
      setJobProfiles((prev) => prev.filter((jp) => !deletedJPIds.has(jp._id)));
      setTestSets((prev) => prev.filter((ts) => !deletedTSIds.has(ts._id)));
      setSelectedJPs(new Set());
      setSelectedTSs(new Set());
    } catch (err) {
      setErrorMsg(`Delete failed: ${getErrorMessage(err)}`);
    } finally {
      setDeleting(false);
    }
  };

  const totalSelected = selectedJPs.size + selectedTSs.size;

  return (
    <div className="djp-container">
      <div className="djp-header">
        <h1>Manage Job Profiles &amp; Test Sets</h1>
      </div>

      {/* Search bar */}
      <div className="djp-section">
        <label className="djp-label">Search by name</label>
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            className="djp-input"
            style={{ flex: 1 }}
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
            placeholder="Enter JP or Test Set name (partial match)..."
            disabled={loading}
          />
          <button
            className="djp-btn djp-btn-primary"
            onClick={handleSearch}
            disabled={loading || searchQuery.trim().length < 2}
          >
            {loading ? 'Searching...' : 'Search'}
          </button>
        </div>
      </div>

      {/* Messages */}
      {errorMsg && <div className="djp-error-box">{errorMsg}</div>}
      {successMsg && (
        <div className="djp-result-box" style={{ borderLeftColor: '#22c55e' }}>
          <p style={{ margin: 0, fontWeight: 600, color: '#16a34a' }}>{successMsg}</p>
        </div>
      )}

      {/* Results */}
      {searched && (
        <div className="djp-two-col" style={{ marginTop: 20 }}>
          {/* Job Profiles column */}
          <div className="djp-section">
            <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700 }}>
              Job Profiles ({jobProfiles.length})
            </h3>
            {jobProfiles.length === 0 ? (
              <p style={{ color: '#64748b', fontSize: 13 }}>No Job Profiles found</p>
            ) : (
              <div className="djp-name-list-selectable" style={{ maxHeight: 400 }}>
                {jobProfiles.map((jp) => (
                  <div
                    key={jp._id}
                    className={`djp-name-item ${selectedJPs.has(jp._id) ? 'selected' : ''}`}
                    onClick={() => toggleJP(jp._id)}
                  >
                    <span className="djp-checkbox-dot">
                      {selectedJPs.has(jp._id) ? '✓' : ''}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, wordBreak: 'break-all' }}>
                        {jp.name}
                      </div>
                      {jp.description && (
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                          {jp.description.slice(0, 80)}
                        </div>
                      )}
                      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
                        ID: {jp._id}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Test Sets column */}
          <div className="djp-section">
            <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700 }}>
              Test Sets ({testSets.length})
            </h3>
            {testSets.length === 0 ? (
              <p style={{ color: '#64748b', fontSize: 13 }}>No Test Sets found</p>
            ) : (
              <div className="djp-name-list-selectable" style={{ maxHeight: 400 }}>
                {testSets.map((ts) => (
                  <div
                    key={ts._id}
                    className={`djp-name-item ${selectedTSs.has(ts._id) ? 'selected' : ''}`}
                    onClick={() => toggleTS(ts._id)}
                  >
                    <span className="djp-checkbox-dot">
                      {selectedTSs.has(ts._id) ? '✓' : ''}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, wordBreak: 'break-all' }}>
                        {ts.name}
                      </div>
                      {ts.description && (
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                          {ts.description.slice(0, 80)}
                        </div>
                      )}
                      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
                        ID: {ts._id}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Delete button */}
      {searched && (jobProfiles.length > 0 || testSets.length > 0) && (
        <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', gap: 16 }}>
          <button
            className="djp-btn djp-btn-danger"
            onClick={handleDelete}
            disabled={deleting || totalSelected === 0}
          >
            {deleting
              ? 'Deleting...'
              : totalSelected > 0
              ? `Delete ${totalSelected} selected item(s)`
              : 'Select items to delete'}
          </button>
          {totalSelected > 0 && (
            <span style={{ fontSize: 13, color: '#64748b' }}>
              {selectedJPs.size > 0 && `${selectedJPs.size} JP(s)`}
              {selectedJPs.size > 0 && selectedTSs.size > 0 && ', '}
              {selectedTSs.size > 0 && `${selectedTSs.size} TS(s)`}
            </span>
          )}
        </div>
      )}

      {/* Delete results detail */}
      {deleteResults && (
        <div className="djp-section" style={{ marginTop: 20 }}>
          <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700 }}>Delete Results</h3>
          {(deleteResults.job_profiles || []).map((r) => (
            <div
              key={r._id}
              style={{
                padding: '8px 12px',
                marginBottom: 6,
                borderRadius: 6,
                fontSize: 13,
                background: r.success ? '#f0fdf4' : '#fef2f2',
                color: r.success ? '#16a34a' : '#dc2626',
                border: `1px solid ${r.success ? '#bbf7d0' : '#fecaca'}`,
              }}
            >
              <strong>JP</strong> {r._id}: {r.message}
            </div>
          ))}
          {(deleteResults.test_sets || []).map((r) => (
            <div
              key={r._id}
              style={{
                padding: '8px 12px',
                marginBottom: 6,
                borderRadius: 6,
                fontSize: 13,
                background: r.success ? '#f0fdf4' : '#fef2f2',
                color: r.success ? '#16a34a' : '#dc2626',
                border: `1px solid ${r.success ? '#bbf7d0' : '#fecaca'}`,
              }}
            >
              <strong>TS</strong> {r._id}: {r.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
