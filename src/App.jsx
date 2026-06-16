import React, { useState, useEffect } from 'react';
import RegressionHome from './RegressionHome';
import RunPlan from './pages/RunPlan';
import Handover from './pages/Handover';
import TestcaseManagement from './pages/TestcaseManagement';
import TriageGenie from './pages/TriageGenie';
import RunReport from './pages/RunReport';
import DynamicJobProfile from './pages/DynamicJobProfile';
import ManageJobProfile from './pages/ManageJobProfile';
import FailedTestcaseAnalysis from './pages/FailedTestcaseAnalysis';
import { TaskProvider } from './context/TaskContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import TaskStatusIcon from './components/TaskStatusIcon';
import LoginPage from './components/LoginPage';
import './App.css';

function Dashboard() {
  const { user, logout } = useAuth();
  const [activePage, setActivePage] = useState('home');
  const [menuVisible, setMenuVisible] = useState(true);

  useEffect(() => {
    const handleSetActivePage = (event) => {
      setActivePage(event.detail);
    };
    window.addEventListener('setActivePage', handleSetActivePage);
    return () => {
      window.removeEventListener('setActivePage', handleSetActivePage);
    };
  }, []);

  const menuItems = [
    { id: 'home', label: 'Home', icon: '🏠', description: 'Regression Overview' },
    { id: 'run-plan', label: 'Run Plan', icon: '📅', description: 'Regression Scheduling' },
    { id: 'handover', label: 'Handover', icon: '📤', description: 'New Testcase Onboarding' },
    { id: 'testcase', label: 'Testcase Management', icon: '📋', description: 'Testcase Management' },
    { id: 'triage-genie', label: 'Triage Genie', icon: '🤖', description: 'Automated Failure Triage' },
    { id: 'failed-analysis', label: 'Failed Testcase Analysis', icon: '🔍', description: 'AI-Powered Failure Analysis' },
    { id: 'run-report', label: 'Run Report', icon: '📊', description: 'QI Analysis' },
    { id: 'job-profile', label: 'Dynamic Job Profile', icon: '⚙️', description: 'Job Profile Creation' },
    { id: 'manage-jp', label: 'Manage JP / TS', icon: '🗑️', description: 'Search & Delete JP/TS' },
  ];

  const renderPage = () => {
    switch (activePage) {
      case 'home':
        return <RegressionHome />;
      case 'run-plan':
        return <RunPlan />;
      case 'handover':
        return <Handover />;
      case 'testcase':
        return <TestcaseManagement />;
      case 'triage-genie':
        return <TriageGenie />;
      case 'failed-analysis':
        return <FailedTestcaseAnalysis />;
      case 'run-report':
        return <RunReport />;
      case 'job-profile':
        return <DynamicJobProfile />;
      case 'manage-jp':
        return <ManageJobProfile />;
      default:
        return <RegressionHome />;
    }
  };

  const displayName = user?.name || user?.sub || 'User';

  return (
    <TaskProvider>
      <div className="app-container">
        <nav className={`sidebar ${menuVisible ? '' : 'collapsed'}`}>
          <div className="sidebar-header">
            <h2>Regression Dashboard</h2>
            <button
              className="menu-toggle-btn"
              onClick={() => setMenuVisible(!menuVisible)}
              title={menuVisible ? 'Hide Menu' : 'Show Menu'}
            >
              {menuVisible ? '◀' : '▶'}
            </button>
          </div>
          <div className="sidebar-user-info">
            <span className="sidebar-user-name" title={user?.email || ''}>
              {displayName}
            </span>
            <button className="sidebar-logout-btn" onClick={logout} title="Sign out">
              Logout
            </button>
          </div>
          <ul className="menu-list">
            {menuItems.map((item) => (
              <li
                key={item.id}
                className={`menu-item ${activePage === item.id ? 'active' : ''}`}
                onClick={() => setActivePage(item.id)}
              >
                <span className="menu-icon">{item.icon}</span>
                <div className="menu-content">
                  <span className="menu-label">{item.label}</span>
                  <span className="menu-description">{item.description}</span>
                </div>
              </li>
            ))}
          </ul>
        </nav>

        {!menuVisible && (
          <button
            className="floating-menu-toggle"
            onClick={() => setMenuVisible(true)}
            title="Show Menu"
          >
            ☰
          </button>
        )}

        <main className={`main-content ${menuVisible ? '' : 'expanded'}`}>
          {renderPage()}
        </main>

        <TaskStatusIcon />
      </div>
    </TaskProvider>
  );
}

function AppGate() {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="login-wrapper" style={{ color: 'rgba(255,255,255,0.6)', fontSize: 16 }}>
        Loading...
      </div>
    );
  }

  return isAuthenticated ? <Dashboard /> : <LoginPage />;
}

function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  );
}

export default App;
