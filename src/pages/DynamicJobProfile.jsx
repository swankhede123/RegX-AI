import React, { useState, useRef, useCallback, useEffect } from 'react';
import axios from 'axios';
import { API_BASE_URL } from '../config';
import './DynamicJobProfile.css';

const API_BASE = `${API_BASE_URL}/mcp/regression/dynamic-jp`;

const derivePcBranch = (branch) =>
  branch.trim().toLowerCase() === 'master' ? 'master' : `${branch}-pc`;

const buildDefaultConfig = (branchName = 'master') => ({
  nosBranch: branchName,
  nosTag: 'Latest Smoke Passed',
  pcBranch: derivePcBranch(branchName),
  pcTag: 'Latest Smoke Passed',
  nutestBranch: branchName,
  provider: 'global_pool',
  resourceType: 'nested_2.0',
  nodePool: [],
  frameworkPatchUrl: '',
  testPatchUrl: '',
});

const getReleaseType = (branchName) =>
  branchName.trim().toLowerCase() === 'master' ? 'opt' : 'release';

const RESOURCE_TYPE_OPTIONS = [
  { value: 'nested_2.0', label: 'NestedAHV 2.0' },
  { value: 'nested_1.0', label: 'NestedAHV 1.0' },
  { value: 'physical',   label: 'Physical' },
];

/** Local calendar YYYYMMDD — matches backend `dyn_name_date` for User_Dyn_<date>_JP_/TS_ names. */
const formatLocalYyyymmdd = () => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}${m}${day}`;
};

export default function DynamicJobProfile() {
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const [showExisting, setShowExisting] = useState(false);
  const [testcaseInput, setTestcaseInput] = useState('');
  const [branch, setBranch] = useState('master');

  // Search results (showExisting mode)
  const [execHistoryFetched, setExecHistoryFetched] = useState(false);
  const [uniquePairs, setUniquePairs] = useState([]);

  // Selected source JP / TS
  const [selectedJP, setSelectedJP] = useState(null);
  const [selectedJPName, setSelectedJPName] = useState('');
  const [selectedTestSetName, setSelectedTestSetName] = useState('');
  const [testSetDetails, setTestSetDetails] = useState(null);
  const [resolvedJPId, setResolvedJPId] = useState(null);
  const [resolvedTSId, setResolvedTSId] = useState(null);
  const [resolving, setResolving] = useState(false);

  // Custom names for the new JP and TS
  const [customJPName, setCustomJPName] = useState('');
  const [customTSName, setCustomTSName] = useState('');
  /** Clone mode only: link new JP to source TS instead of creating a TS with only the typed testcases. */
  const [reuseSourceTS, setReuseSourceTS] = useState(false);
  /** Keeps the last "New Testset" name if user toggles to Use Existing (field hidden) and back. */
  const newTestSetNameWhenNewMode = useRef('');

  // Tag support
  const [showTagInput, setShowTagInput] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [jpTags, setJpTags] = useState([]);

  // Config for fresh creation
  const [config, setConfig] = useState(() => buildDefaultConfig(branch));
  const [nextJPNum, setNextJPNum] = useState(1);
  const [nextTSNum, setNextTSNum] = useState(1);
  const [createResult, setCreateResult] = useState(null);
  const [readyToConfigure, setReadyToConfigure] = useState(false);

  // Patch toggle and search helpers
  const [showPatch, setShowPatch] = useState(false);
  const [nodePoolSearch, setNodePoolSearch] = useState('');
  const [nodePoolResults, setNodePoolResults] = useState([]);
  const [nodePoolLoading, setNodePoolLoading] = useState(false);
  const nodePoolReqId = useRef(0);
  const nodePoolDebounce = useRef(null);

  const [clusterSearch, setClusterSearch] = useState('');
  const [clusterResults, setClusterResults] = useState([]);
  const [clusterLoading, setClusterLoading] = useState(false);
  const clusterReqId = useRef(0);
  const clusterDebounce = useRef(null);

  const [branchResults, setBranchResults] = useState([]);
  const [branchLoading, setBranchLoading] = useState(false);
  const branchReqId = useRef(0);
  const branchDebounce = useRef(null);

  useEffect(() => {
    if (!reuseSourceTS) {
      newTestSetNameWhenNewMode.current = customTSName;
    }
  }, [customTSName, reuseSourceTS]);

  const parseTestcaseNames = () =>
    testcaseInput
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

  const getErrorMessage = (error) => {
    if (error.response?.data?.error) return error.response.data.error;
    if (error.response?.status === 503) return 'Backend is unreachable. Is the Flask server running?';
    if (error.response?.status === 504) return 'Request timed out. JITA may be slow or unreachable.';
    if (error.code === 'ERR_NETWORK') return 'Network error. Check your connection and ensure the backend is running.';
    if (error.code === 'ECONNABORTED') return 'Request timed out.';
    return error.message || 'An unknown error occurred';
  };

  const resetSelections = () => {
    setSelectedJP(null);
    setSelectedJPName('');
    setSelectedTestSetName('');
    setTestSetDetails(null);
    setResolvedJPId(null);
    setResolvedTSId(null);
    setCustomJPName('');
    setCustomTSName('');
    setJpTags([]);
    setTagInput('');
    setShowTagInput(false);
    setReuseSourceTS(false);
  };

  const fetchNextNumbers = async () => {
    const dynNameDate = formatLocalYyyymmdd();
    const fallbackJpPrefix = `User_Dyn_${dynNameDate}_JP_`;
    const fallbackTsPrefix = `User_Dyn_${dynNameDate}_TS_`;
    try {
      const response = await axios.post(`${API_BASE}/check-existing`, {
        dyn_name_date: dynNameDate,
      });
      const data = response.data || {};
      const jpNum = typeof data.next_jp_number === 'number' ? data.next_jp_number : 1;
      const tsNum = typeof data.next_ts_number === 'number' ? data.next_ts_number : 1;
      const jpPrefix = typeof data.jp_name_prefix === 'string' && data.jp_name_prefix
        ? data.jp_name_prefix
        : fallbackJpPrefix;
      const tsPrefix = typeof data.ts_name_prefix === 'string' && data.ts_name_prefix
        ? data.ts_name_prefix
        : fallbackTsPrefix;
      setNextJPNum(jpNum);
      setNextTSNum(tsNum);
      return { jpNum, tsNum, jpPrefix, tsPrefix };
    } catch (_) {
      return { jpNum: nextJPNum, tsNum: nextTSNum, jpPrefix: fallbackJpPrefix, tsPrefix: fallbackTsPrefix };
    }
  };

  /** Apply suggested User_Dyn names from a check-existing result (no extra network). */
  const applyNamesFromCheckData = (d, jpName, tsName) => {
    if (jpName) {
      setCustomJPName(`${d.jpPrefix}${d.jpNum}_${jpName}`);
    } else {
      setCustomJPName(`${d.jpPrefix}${d.jpNum}`);
    }
    if (tsName) {
      setCustomTSName(`${d.tsPrefix}${d.tsNum}_${tsName}`);
    } else {
      setCustomTSName(`${d.tsPrefix}${d.tsNum}`);
    }
  };

  const clearJPAndTSSelection = (e) => {
    if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
    setSelectedJP(null);
    setSelectedJPName('');
    setResolvedJPId(null);
    setSelectedTestSetName('');
    setResolvedTSId(null);
    setTestSetDetails(null);
    setErrorMsg(null);
    (async () => {
      const d = await fetchNextNumbers();
      setCustomJPName(`${d.jpPrefix}${d.jpNum}`);
      setCustomTSName(`${d.tsPrefix}${d.tsNum}`);
    })();
  };

  const handleSelectJP = async (jpName) => {
    const tsName = selectedTestSetName || null;
    setSelectedJPName(jpName);
    setResolvedJPId(null);
    setResolving(true);
    setErrorMsg(null);
    try {
      const [resp, d] = await Promise.all([
        axios.post(`${API_BASE}/resolve-names`, { jp_name: jpName }),
        fetchNextNumbers(),
      ]);
      if (resp.data?.jp?._id) {
        setSelectedJP(resp.data.jp._id);
        setResolvedJPId(resp.data.jp._id);
        applyNamesFromCheckData(d, jpName, tsName);
      } else {
        setSelectedJP(null);
        setErrorMsg(`Could not resolve Job Profile "${jpName}" to an ID. It may not exist in JITA.`);
      }
    } catch (err) {
      if (err.response?.status === 404) {
        setErrorMsg('Backend needs restart — the resolve-names endpoint is not loaded yet.');
      } else {
        setErrorMsg(`Failed to resolve JP name: ${getErrorMessage(err)}`);
      }
    } finally {
      setResolving(false);
    }
  };

  const handleSelectTS = async (tsName) => {
    const jpName = selectedJPName || null;
    setSelectedTestSetName(tsName);
    setResolvedTSId(null);
    setTestSetDetails(null);
    setResolving(true);
    setErrorMsg(null);
    try {
      const [resp, d] = await Promise.all([
        axios.post(`${API_BASE}/resolve-names`, { ts_name: tsName }),
        fetchNextNumbers(),
      ]);
      if (resp.data?.ts?._id) {
        setResolvedTSId(resp.data.ts._id);
        setTestSetDetails(resp.data.ts);
        applyNamesFromCheckData(d, jpName, tsName);
      } else {
        setErrorMsg(`Could not resolve Test Set "${tsName}" to an ID. It may not exist in JITA.`);
      }
    } catch (err) {
      if (err.response?.status === 404) {
        setErrorMsg('Backend needs restart — the resolve-names endpoint is not loaded yet.');
      } else {
        setErrorMsg(`Failed to resolve TS name: ${getErrorMessage(err)}`);
      }
    } finally {
      setResolving(false);
    }
  };

  const handleAddTag = () => {
    const tag = tagInput.trim();
    if (tag && !jpTags.includes(tag)) {
      setJpTags([...jpTags, tag]);
    }
    setTagInput('');
  };

  const handleRemoveTag = (tag) => {
    setJpTags(jpTags.filter(t => t !== tag));
  };

  const handleSearch = async () => {
    const names = parseTestcaseNames();
    if (names.length === 0 && showExisting) {
      setErrorMsg('Please enter at least one testcase name');
      return;
    }

    setCreateResult(null);
    setErrorMsg(null);

    if (showExisting) {
      setLoading(true);
      setExecHistoryFetched(false);
      setUniquePairs([]);
      resetSelections();
      try {
        const [histResp, numData] = await Promise.all([
          axios.post(`${API_BASE}/test-execution-history`, {
            test_name: names[0],
            page: 1,
            limit: 200,
            sort: '-start_time',
            branch: branch || '',
          }),
          fetchNextNumbers(),
        ]);
        const data = histResp.data || {};
        setUniquePairs(Array.isArray(data.unique_pairs) ? data.unique_pairs : []);
        setExecHistoryFetched(true);
        const num = Math.max(numData.jpNum, numData.tsNum);
        setCustomJPName(`${numData.jpPrefix}${num}`);
        setCustomTSName(`${numData.tsPrefix}${num}`);
        setReadyToConfigure(true);
      } catch (error) {
        console.error('Error fetching execution history:', error);
        setErrorMsg(`Failed to fetch test history: ${getErrorMessage(error)}`);
      } finally {
        setLoading(false);
      }
    } else {
      resetSelections();
      setReadyToConfigure(true);
      const numData = await fetchNextNumbers();
      const num = Math.max(numData.jpNum, numData.tsNum);
      setCustomJPName(`${numData.jpPrefix}${num}`);
      setCustomTSName(`${numData.tsPrefix}${num}`);
    }
  };

  const handleApplyLatest = () => {
    setConfig(buildDefaultConfig(branch));
    setShowPatch(false);
  };

  const handleSearchNodePools = useCallback((query) => {
    setNodePoolSearch(query);
    if (nodePoolDebounce.current) clearTimeout(nodePoolDebounce.current);
    if (!query || query.length < 2) {
      setNodePoolResults([]);
      setNodePoolLoading(false);
      return;
    }
    setNodePoolLoading(true);
    nodePoolDebounce.current = setTimeout(async () => {
      const reqId = ++nodePoolReqId.current;
      try {
        const response = await axios.post(`${API_BASE}/search-node-pools`, { query });
        if (reqId === nodePoolReqId.current) {
          setNodePoolResults(Array.isArray(response.data?.pools) ? response.data.pools : []);
        }
      } catch (_) {
        if (reqId === nodePoolReqId.current) setNodePoolResults([]);
      } finally {
        if (reqId === nodePoolReqId.current) setNodePoolLoading(false);
      }
    }, 300);
  }, []);

  const handleSearchClusters = useCallback((query) => {
    setClusterSearch(query);
    if (clusterDebounce.current) clearTimeout(clusterDebounce.current);
    if (!query || query.length < 2) {
      setClusterResults([]);
      setClusterLoading(false);
      return;
    }
    setClusterLoading(true);
    clusterDebounce.current = setTimeout(async () => {
      const reqId = ++clusterReqId.current;
      try {
        const response = await axios.post(`${API_BASE}/search-clusters`, { query });
        if (reqId === clusterReqId.current) {
          setClusterResults(Array.isArray(response.data?.clusters) ? response.data.clusters : []);
        }
      } catch (_) {
        if (reqId === clusterReqId.current) setClusterResults([]);
      } finally {
        if (reqId === clusterReqId.current) setClusterLoading(false);
      }
    }, 300);
  }, []);

  const handleSearchBranches = useCallback((query) => {
    if (branchDebounce.current) clearTimeout(branchDebounce.current);
    if (!query || query.length < 2) {
      setBranchResults([]);
      setBranchLoading(false);
      return;
    }
    setBranchLoading(true);
    branchDebounce.current = setTimeout(async () => {
      const reqId = ++branchReqId.current;
      try {
        const response = await axios.post(`${API_BASE}/search-branches`, { query });
        if (reqId === branchReqId.current) {
          setBranchResults(Array.isArray(response.data?.branches) ? response.data.branches : []);
        }
      } catch (_) {
        if (reqId === branchReqId.current) setBranchResults([]);
      } finally {
        if (reqId === branchReqId.current) setBranchLoading(false);
      }
    }, 300);
  }, []);

  const handleCreate = async () => {
    const names = parseTestcaseNames();
    if (names.length === 0 && !(showExisting && reuseSourceTS)) {
      setErrorMsg('Please enter at least one testcase name (or enable “Use existing test set” in clone mode)');
      return;
    }
    if (showExisting && !selectedJP) {
      setErrorMsg('Please select a source Job Profile from the list');
      return;
    }

    setLoading(true);
    setCreateResult(null);
    setErrorMsg(null);
    try {
      const allTags = [...new Set(jpTags)];

      const response = await axios.post(`${API_BASE}/create`, {
        source_jp_id: showExisting ? selectedJP : null,
        source_testset_id: showExisting ? (resolvedTSId || testSetDetails?._id || null) : null,
        source_testset_name: showExisting ? (selectedTestSetName || null) : null,
        nos_branch: config.nosBranch || 'master',
        nos_tag: config.nosTag || 'Latest Smoke Passed',
        pc_branch: config.pcBranch || 'master',
        pc_tag: config.pcTag || 'Latest Smoke Passed',
        nutest_branch: config.nutestBranch || 'master',
        provider: config.provider || 'global_pool',
        resource_type: config.resourceType || 'nested_2.0',
        node_pool: Array.isArray(config.nodePool) ? config.nodePool : [],
        framework_patch_url: showPatch ? (config.frameworkPatchUrl || '') : '',
        test_patch_url: showPatch ? (config.testPatchUrl || '') : '',
        testcase_names: names,
        create_fresh: !showExisting,
        custom_jp_name: customJPName || null,
        custom_ts_name: customTSName || null,
        dyn_name_date: formatLocalYyyymmdd(),
        jp_tags: allTags.length > 0 ? allTags : [],
        reuse_source_ts: !!(showExisting && reuseSourceTS),
      });
      if (response.data?.success) {
        setCreateResult(response.data);
      } else {
        setErrorMsg(response.data?.error || 'Creation returned without success flag');
      }
    } catch (error) {
      console.error('Error creating dynamic profile:', error);
      const serverMsg = error?.response?.data?.error;
      if (serverMsg) {
        setErrorMsg(serverMsg);
      } else {
        setErrorMsg(`Failed to create dynamic profile: ${getErrorMessage(error)}`);
      }
    } finally {
      setLoading(false);
    }
  };

  // Shared tag input UI used by both clone and fresh modes
  const renderErrorMsg = () =>
    errorMsg ? (
      <div className="djp-inline-error">
        <span>{errorMsg}</span>
        <button onClick={() => setErrorMsg(null)} title="Dismiss">&times;</button>
      </div>
    ) : null;

  /** Add tags and Patch toggles on one row; tag fields then patch fields open below. */
  const renderTagsAndPatchSection = () => (
    <div className="djp-tag-section">
      <div className="djp-tag-patch-toggles" role="group" aria-label="Tags and patches">
        <div className="djp-toggle-row djp-toggle-row--inline">
          <label>Add tags</label>
          <div
            className={`djp-toggle ${showTagInput ? 'active' : ''}`}
            onClick={() => setShowTagInput(!showTagInput)}
            role="switch"
            aria-checked={showTagInput}
          >
            <div className="djp-toggle-knob" />
          </div>
        </div>
        <div className="djp-toggle-row djp-toggle-row--inline">
          <label>Patch</label>
          <div
            className={`djp-toggle ${showPatch ? 'active' : ''}`}
            onClick={() => setShowPatch(!showPatch)}
            role="switch"
            aria-checked={showPatch}
          >
            <div className="djp-toggle-knob" />
          </div>
        </div>
      </div>
      {showTagInput && (
        <div className="djp-tag-input-area djp-tag-patch-expand">
          <div className="djp-tag-input-row">
            <input
              type="text"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddTag(); } }}
              placeholder="Type a tag and press Enter"
            />
            <button
              className="djp-btn djp-btn-primary djp-btn-sm"
              onClick={handleAddTag}
              disabled={!tagInput.trim()}
            >
              Add
            </button>
          </div>
          {jpTags.length > 0 && (
            <div className="djp-tag-chips">
              {jpTags.map((tag) => (
                <span key={tag} className="djp-tag-chip">
                  {tag}
                  <button onClick={() => handleRemoveTag(tag)} title="Remove tag">&times;</button>
                </span>
              ))}
            </div>
          )}
          <small style={{ color: '#64748b' }}>Tags will be added to the JP's advanced options</small>
        </div>
      )}
      {showPatch && (
        <div className="djp-patch-fields djp-tag-patch-expand">
          <div className="djp-form-group">
            <label>Framework Patch URL</label>
            <input
              type="text"
              value={config.frameworkPatchUrl}
              onChange={(e) => setConfig({ ...config, frameworkPatchUrl: e.target.value })}
              placeholder="https://nugerrit.ntnxdpro.com/changes/nutest-py3~.../patch?zip"
            />
          </div>
          <div className="djp-form-group">
            <label>Nutest-Py3-Tests Patch URL</label>
            <input
              type="text"
              value={config.testPatchUrl}
              onChange={(e) => setConfig({ ...config, testPatchUrl: e.target.value })}
              placeholder="https://nugerrit.ntnxdpro.com/changes/nutest-py3-tests~.../patch?zip"
            />
          </div>
        </div>
      )}
    </div>
  );

  // Shared result box UI
  const renderResultBox = (title) => {
    if (!createResult?.success) return null;
    return (
      <div className="djp-result-box">
        <h3>{title}</h3>
        <p>
          Job Profile: <code>{createResult.job_profile?.name || 'Unknown'}</code>
          {createResult.job_profile?._id && (
            <> &mdash; <a
              href={`https://jita.eng.nutanix.com/job_profiles/${createResult.job_profile._id}`}
              target="_blank"
              rel="noreferrer"
              style={{ color: '#3498db' }}
            >
              View in JITA
            </a></>
          )}
        </p>
        {createResult.test_set && (
          <p>
            Test Set: <code>{createResult.test_set.name || 'Unknown'}</code>
            {createResult.test_set.reused && (
              <span className="djp-info-banner info" style={{ display: 'inline', marginLeft: '8px', padding: '2px 8px', fontSize: '11px' }}>
                Already existed &mdash; reused
              </span>
            )}
            {createResult.test_set._id && (
              <span style={{ color: '#7f8c8d', marginLeft: '8px', fontSize: '12px' }}>ID: {createResult.test_set._id}</span>
            )}
          </p>
        )}
        {!createResult.test_set && (
          <p style={{ color: '#856404', fontSize: '13px' }}>
            Note: No test set was created (test set creation may have failed).
          </p>
        )}
        <p style={{ color: '#7f8c8d', fontSize: '13px' }}>{createResult.message || ''}</p>
        {createResult.warnings?.length > 0 && (
          <div className="djp-info-banner warning" style={{ marginTop: '10px' }}>
            <strong>Warnings:</strong>
            <ul style={{ margin: '5px 0 0 0', paddingLeft: '20px' }}>
              {createResult.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="djp-container">
      <div className="djp-header">
        <h1>Dynamic Job Profile Creation</h1>
      </div>

      {/* Error messages are displayed below the Create buttons */}

      {/* Step 1: Testcase input + Branch + Toggle */}
      <div className="djp-section">
        <h3 className="djp-section-title">Step 1: Enter Testcase Names</h3>
        <div className="djp-form-group">
          <textarea
            value={testcaseInput}
            onChange={(e) => setTestcaseInput(e.target.value)}
            placeholder="Enter fully qualified testcase names (space, comma, or line break between names)&#10;e.g.&#10;cdp.stargate.storage_policy.api.test_storage_policy.TestStoragePolicy.test_storage_policy___duplicate_name"
            rows={4}
          />
          <small>Space, comma, or newline between testcase names</small>
        </div>

        <div className="djp-search-row">
          <div className="djp-form-group" style={{ flex: 1, minWidth: 0, position: 'relative' }}>
            <label>Branch</label>
            <input
              type="text"
              value={branch}
              onChange={(e) => {
                const val = e.target.value;
                setBranch(val);
                setConfig(prev => ({
                  ...prev,
                  nosBranch: val,
                  pcBranch: derivePcBranch(val),
                  nutestBranch: val,
                }));
                handleSearchBranches(val);
              }}
              onBlur={() => setTimeout(() => setBranchResults([]), 150)}
              placeholder="Type to search (e.g., master, ganges-7.6)"
            />
            {branchLoading && <small style={{ color: '#64748b' }}>Searching...</small>}
            {branchResults.length > 0 && (
              <div className="djp-pool-results" style={{ position: 'absolute', zIndex: 10, left: 0, right: 0, marginTop: '2px' }}>
                {branchResults.map((b) => (
                  <div
                    key={b}
                    className={`djp-pool-item ${branch === b ? 'selected' : ''}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      setBranch(b);
                      setConfig(prev => ({
                        ...prev,
                        nosBranch: b,
                        pcBranch: derivePcBranch(b),
                        nutestBranch: b,
                      }));
                      setBranchResults([]);
                    }}
                  >
                    {b}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="djp-toggle-group">
            <label className="djp-toggle-label">Show Existing</label>
            <div
              className={`djp-toggle ${showExisting ? 'active' : ''}`}
              onClick={() => {
                setShowExisting(!showExisting);
                setReadyToConfigure(false);
                setExecHistoryFetched(false);
                setUniquePairs([]);
                resetSelections();
                setCreateResult(null);
              }}
            >
              <div className="djp-toggle-knob" />
            </div>
            <small className="djp-toggle-hint">
              {showExisting ? 'Show execution history' : 'Create new JP & test set'}
            </small>
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button
              className="djp-btn djp-btn-primary"
              onClick={handleSearch}
              disabled={loading || (showExisting && !testcaseInput.trim())}
            >
              {loading ? 'Searching...' : showExisting ? 'Search History' : 'Proceed'}
            </button>
          </div>
        </div>
        {!showExisting && (
          <small className="djp-toggle-hint" style={{ display: 'block', marginTop: '8px' }}>
            In direct create you can proceed with an empty testcase list and add names later in the box above.
          </small>
        )}
      </div>

      {/* Clone mode: selectable JP & TS lists */}
      {showExisting && execHistoryFetched && (() => {
        const tsSet = new Set();
        const jpSet = new Set();
        for (const p of uniquePairs) {
          if (p.test_set) tsSet.add(p.test_set.trim());
          if (p.job_profile) jpSet.add(p.job_profile.trim());
        }
        const uniqueTS = [...tsSet].sort();
        const uniqueJP = [...jpSet].sort();
        return (
          <div className="djp-section">
            {uniqueTS.length === 0 && uniqueJP.length === 0 ? (
              <div className="djp-info-banner warning">
                No test sets or job profiles found for this testcase.
              </div>
            ) : (
              <>
                <h3 className="djp-section-title">Step 2: Select Source JP & Test Set to Clone</h3>
                {(selectedJPName || selectedTestSetName || resolvedJPId || resolvedTSId) && (
                  <div className="djp-clear-selection-row">
                    <button
                      type="button"
                      className="djp-btn djp-btn-secondary"
                      style={{ fontSize: '12px', padding: '4px 10px' }}
                      onClick={clearJPAndTSSelection}
                    >
                      Clear selection
                    </button>
                  </div>
                )}
                <div className="djp-unique-lists">
                  <div className="djp-unique-list-col">
                    <div className="djp-list-heading-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                      <h4 className="djp-list-heading" style={{ margin: 0 }}>
                        Test Sets <span className="djp-list-count">{uniqueTS.length}</span>
                      </h4>
                    </div>
                    <ul className="djp-name-list djp-name-list-selectable">
                      {uniqueTS.map((name, i) => (
                        <li
                          key={i}
                          className={`djp-name-item ${selectedTestSetName === name ? 'selected' : ''}`}
                          onClick={() => handleSelectTS(name)}
                        >
                          <span className="djp-radio-dot">
                            {selectedTestSetName === name && <span className="djp-radio-dot-inner" />}
                          </span>
                          <span className="djp-name-text">{name}</span>
                          {selectedTestSetName === name && resolvedTSId && (
                            <span className="djp-resolved-badge">ID: {resolvedTSId.slice(-8)}</span>
                          )}
                          {selectedTestSetName === name && resolving && (
                            <span className="djp-resolving-text">resolving...</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="djp-unique-list-col">
                    <div className="djp-list-heading-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                      <h4 className="djp-list-heading" style={{ margin: 0 }}>
                        Job Profiles <span className="djp-list-count">{uniqueJP.length}</span>
                      </h4>
                    </div>
                    <ul className="djp-name-list djp-name-list-selectable">
                      {uniqueJP.map((name, i) => (
                        <li
                          key={i}
                          className={`djp-name-item ${selectedJPName === name ? 'selected' : ''}`}
                          onClick={() => handleSelectJP(name)}
                        >
                          <span className="djp-radio-dot">
                            {selectedJPName === name && <span className="djp-radio-dot-inner" />}
                          </span>
                          <span className="djp-name-text">{name}</span>
                          {selectedJPName === name && resolvedJPId && (
                            <span className="djp-resolved-badge">ID: {resolvedJPId.slice(-8)}</span>
                          )}
                          {selectedJPName === name && resolving && (
                            <span className="djp-resolving-text">resolving...</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>

                <div className="djp-clone-config">
                  <h4 className="djp-list-heading" style={{ marginTop: '20px', marginBottom: '12px' }}>
                    Test set for cloned job profile
                  </h4>
                  <div className="djp-ts-mode-row" role="radiogroup" aria-label="Test set for cloned job profile">
                    <label className="djp-ts-mode-option">
                      <input
                        type="radio"
                        name="djp-ts-mode"
                        checked={!reuseSourceTS}
                        onChange={() => {
                          setReuseSourceTS(false);
                          if (newTestSetNameWhenNewMode.current) {
                            setCustomTSName(newTestSetNameWhenNewMode.current);
                          }
                        }}
                      />
                      <span>
                        <strong>New Testset</strong> — contains only the testcase above, copies <code>test_args</code> /{' '}
                        <code>framework_args</code>
                      </span>
                    </label>
                    <label className="djp-ts-mode-option">
                      <input
                        type="radio"
                        name="djp-ts-mode"
                        checked={reuseSourceTS}
                        onChange={() => {
                          newTestSetNameWhenNewMode.current = customTSName;
                          setReuseSourceTS(true);
                        }}
                      />
                      <span>
                        <strong>Use Existing Testset</strong> — no clone; new job profile uses that test set. pick a
                        test set below, or clear ts selection to use the source job profile’s first test set
                      </span>
                    </label>
                  </div>
                  <h4 className="djp-list-heading" style={{ marginTop: '8px', marginBottom: '12px' }}>
                    {reuseSourceTS ? 'New job profile name' : 'New JP & TS names'}
                  </h4>
                  {reuseSourceTS ? (
                    <div className="djp-form-group" style={{ maxWidth: '520px' }}>
                      <label>New job profile name</label>
                      <input
                        type="text"
                        value={customJPName}
                        onChange={(e) => setCustomJPName(e.target.value)}
                        placeholder="e.g., User_Dyn_20260424_JP_1"
                      />
                      {selectedJPName && (
                        <small>Cloning <strong>JP</strong></small>
                      )}
                      {selectedTestSetName && (
                        <small style={{ display: 'block', marginTop: '6px' }}>
                          Test set linked to the new JP
                        </small>
                      )}
                    </div>
                  ) : (
                    <div className="djp-name-editor-row">
                      <div className="djp-form-group" style={{ flex: 1 }}>
                        <label>New TestSet</label>
                        <input
                          type="text"
                          value={customTSName}
                          onChange={(e) => setCustomTSName(e.target.value)}
                          placeholder="e.g., User_Dyn_20260424_TS_1"
                        />
                        {selectedTestSetName && (
                          <small>Cloning <strong>TS</strong></small>
                        )}
                      </div>
                      <div className="djp-form-group" style={{ flex: 1 }}>
                        <label>New Job Profile</label>
                        <input
                          type="text"
                          value={customJPName}
                          onChange={(e) => setCustomJPName(e.target.value)}
                          placeholder="e.g., User_Dyn_20260424_JP_1"
                        />
                        {selectedJPName && (
                          <small>Cloning <strong>JP</strong></small>
                        )}
                      </div>
                    </div>
                  )}
                  {renderTagsAndPatchSection()}
                </div>

                <div className="djp-form-actions">
                  <button
                    className="djp-btn djp-btn-success djp-btn-lg"
                    onClick={handleCreate}
                    disabled={loading || !selectedJP || resolving}
                  >
                    {loading ? 'Cloning...' : 'Clone & Create'}
                  </button>
                  {!selectedJP && (
                    <small style={{ color: '#e74c3c', marginLeft: '12px', alignSelf: 'center' }}>
                      select a source job profile
                    </small>
                  )}
                </div>
                {renderErrorMsg()}
                {renderResultBox('Profile Cloned Successfully')}
              </>
            )}
          </div>
        );
      })()}

      {/* Fresh create: info banner */}
      {!showExisting && readyToConfigure && (
        <div className="djp-section">
          <div className="djp-info-banner info">
            <strong>Direct Create Mode</strong> — A new test set and job profile will be created
            {parseTestcaseNames().length > 0
              ? (
                <>
                  {' '}containing <strong>{parseTestcaseNames().length}</strong> testcase{parseTestcaseNames().length !== 1 ? 's' : ''}.
                </>
                )
              : ' — add testcases above, or use Add Tags below if you need JP tags.'}
          </div>
        </div>
      )}

      {/* Fresh create: configuration */}
      {readyToConfigure && !showExisting && (
        <div className="djp-section">
          <div className="djp-section-title-row">
            <h3 className="djp-section-title">Step 2: Configuration</h3>
            <button
              className="djp-btn djp-btn-latest"
              onClick={handleApplyLatest}
              title={`Auto-fill: Latest Smoke Passed on ${branch}, nutest ${branch}, global_nested_2.0`}
            >
              Latest
            </button>
          </div>

          <div className="djp-clone-config" style={{ marginBottom: '16px' }}>
            <h4 className="djp-list-heading" style={{ marginBottom: '12px' }}>
              New JP &amp; TS Names
            </h4>
            <div className="djp-name-editor-row">
              <div className="djp-form-group" style={{ flex: 1 }}>
                <label>Job Profile Name</label>
                <input
                  type="text"
                  value={customJPName}
                  onChange={(e) => setCustomJPName(e.target.value)}
                  placeholder="e.g., User_Dyn_20260424_JP_1"
                />
              </div>
              <div className="djp-form-group" style={{ flex: 1 }}>
                <label>Test Set Name</label>
                <input
                  type="text"
                  value={customTSName}
                  onChange={(e) => setCustomTSName(e.target.value)}
                  placeholder="e.g., User_Dyn_20260424_TS_1"
                />
              </div>
            </div>
            {renderTagsAndPatchSection()}
          </div>

          <div className="djp-config-panel">
            <div className="djp-config-card">
              <h4>Provider</h4>
              <div className="djp-form-group">
                <label>Type</label>
                <select
                  value={config.provider}
                  onChange={(e) => setConfig({ ...config, provider: e.target.value, nodePool: [] })}
                >
                  <option value="global_pool">Global Pool</option>
                  <option value="node_pool">Private Node Pool</option>
                  <option value="static">Static Resources</option>
                </select>
              </div>

              {config.provider === 'global_pool' && (
                <div className="djp-form-group">
                  <label>Resource Type</label>
                  <select
                    value={config.resourceType}
                    onChange={(e) => setConfig({ ...config, resourceType: e.target.value })}
                  >
                    {RESOURCE_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
              )}

              {config.provider === 'node_pool' && (
                <>
                  <div className="djp-form-group">
                    <label>Search Node Pool</label>
                    <input
                      type="text"
                      value={nodePoolSearch}
                      onChange={(e) => handleSearchNodePools(e.target.value)}
                      placeholder="Type to search (e.g., Regression_CDP)"
                    />
                    {nodePoolLoading && <small style={{ color: '#7f8c8d' }}>Searching...</small>}
                  </div>
                  {nodePoolSearch.length >= 2 && !nodePoolLoading && nodePoolResults.length === 0 && (
                    <small style={{ color: '#e74c3c' }}>No node pools matching "{nodePoolSearch}"</small>
                  )}
                  {nodePoolResults.length > 0 && (
                    <div className="djp-pool-results">
                      {nodePoolResults.map((pool, idx) => {
                        const alreadySelected = config.nodePool.includes(pool);
                        return (
                          <div
                            key={idx}
                            className={`djp-pool-item ${alreadySelected ? 'selected' : ''}`}
                            onClick={() => {
                              if (!alreadySelected) setConfig({ ...config, nodePool: [...config.nodePool, pool] });
                              setNodePoolSearch('');
                              setNodePoolResults([]);
                            }}
                          >
                            {pool}
                            {alreadySelected && <span style={{ marginLeft: '8px', color: '#27ae60', fontSize: '12px' }}>selected</span>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {config.nodePool.length > 0 && (
                    <div className="djp-tag-chips" style={{ marginTop: '8px' }}>
                      {config.nodePool.map((pool) => (
                        <span key={pool} className="djp-node-pool-chip">
                          {pool}
                          <button
                            onClick={() => setConfig({ ...config, nodePool: config.nodePool.filter(p => p !== pool) })}
                            title="Remove"
                          >
                            &times;
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="djp-form-group" style={{ marginTop: '8px' }}>
                    <label>Resource Type</label>
                    <select
                      value={config.resourceType}
                      onChange={(e) => setConfig({ ...config, resourceType: e.target.value })}
                    >
                      {RESOURCE_TYPE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>
                </>
              )}

              {config.provider === 'static' && (
                <>
                  <div className="djp-form-group">
                    <label>Search Cluster / IP</label>
                    <input
                      type="text"
                      value={clusterSearch}
                      onChange={(e) => handleSearchClusters(e.target.value)}
                      placeholder="Type cluster name or IP (e.g., 10.124.83.160)"
                    />
                    {clusterLoading && <small style={{ color: '#7f8c8d' }}>Searching...</small>}
                  </div>
                  {clusterSearch.length >= 2 && !clusterLoading && clusterResults.length === 0 && (
                    <small style={{ color: '#e74c3c' }}>No clusters matching "{clusterSearch}"</small>
                  )}
                  {clusterResults.length > 0 && (
                    <div className="djp-pool-results">
                      {clusterResults.map((cluster, idx) => {
                        const alreadySelected = config.nodePool.includes(cluster.name);
                        return (
                          <div
                            key={idx}
                            className={`djp-pool-item ${alreadySelected ? 'selected' : ''}`}
                            onClick={() => {
                              if (!alreadySelected) setConfig({ ...config, nodePool: [...config.nodePool, cluster.name] });
                              setClusterSearch('');
                              setClusterResults([]);
                            }}
                          >
                            <span style={{ fontWeight: 500 }}>{cluster.name}</span>
                            {cluster.status && (
                              <span style={{
                                marginLeft: '8px', fontSize: '11px', padding: '1px 6px', borderRadius: '8px',
                                background: cluster.status === 'free' ? '#e8f8f0' : '#fef3e5',
                                color: cluster.status === 'free' ? '#27ae60' : '#e67e22',
                              }}>
                                {cluster.status}
                              </span>
                            )}
                            {alreadySelected && <span style={{ marginLeft: '8px', color: '#27ae60', fontSize: '12px' }}>selected</span>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {config.nodePool.length > 0 && (
                    <div className="djp-tag-chips" style={{ marginTop: '8px' }}>
                      {config.nodePool.map((name) => (
                        <span key={name} className="djp-node-pool-chip">
                          {name}
                          <button
                            onClick={() => setConfig({ ...config, nodePool: config.nodePool.filter(p => p !== name) })}
                            title="Remove"
                          >
                            &times;
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="djp-config-card">
              <h4>NOS_CLUSTER</h4>
              <div className="djp-form-group">
                <label>Branch</label>
                <input type="text" value={config.nosBranch} onChange={(e) => setConfig({ ...config, nosBranch: e.target.value })} placeholder="e.g., master" />
              </div>
              <div className="djp-form-group">
                <label>Release Type</label>
                <span className={`djp-release-badge ${getReleaseType(config.nosBranch)}`}>{getReleaseType(config.nosBranch)}</span>
              </div>
              <div className="djp-form-group">
                <label>Tag</label>
                <select value={config.nosTag} onChange={(e) => setConfig({ ...config, nosTag: e.target.value })}>
                  <option value="Latest Smoke Passed">Latest Smoke Passed</option>
                  <option value="Latest DIAL Passed">Latest DIAL Passed</option>
                </select>
              </div>
            </div>

            <div className="djp-config-card">
              <h4>PRISM_CENTRAL</h4>
              <div className="djp-form-group">
                <label>Branch</label>
                <input type="text" value={config.pcBranch} onChange={(e) => setConfig({ ...config, pcBranch: e.target.value })} placeholder="e.g., master" />
              </div>
              <div className="djp-form-group">
                <label>Release Type</label>
                <span className={`djp-release-badge ${getReleaseType(config.pcBranch)}`}>{getReleaseType(config.pcBranch)}</span>
              </div>
              <div className="djp-form-group">
                <label>Tag</label>
                <select value={config.pcTag} onChange={(e) => setConfig({ ...config, pcTag: e.target.value })}>
                  <option value="Latest Smoke Passed">Latest Smoke Passed</option>
                  <option value="Latest DIAL Passed">Latest DIAL Passed</option>
                </select>
              </div>
            </div>

            <div className="djp-config-card">
              <h4>Nutest</h4>
              <div className="djp-form-group">
                <label>Branch</label>
                <input type="text" value={config.nutestBranch} onChange={(e) => setConfig({ ...config, nutestBranch: e.target.value })} placeholder="e.g., master" />
              </div>
            </div>
          </div>

          <div className="djp-form-actions">
            <button
              className="djp-btn djp-btn-success djp-btn-lg"
              onClick={handleCreate}
              disabled={loading}
            >
              {loading ? 'Creating...' : 'Create Job Profile'}
            </button>
          </div>
          {renderErrorMsg()}
          {renderResultBox('Profile Created Successfully')}
        </div>
      )}
    </div>
  );
}
