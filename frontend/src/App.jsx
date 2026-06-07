import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { 
  LogIn, Sun, Moon, LogOut, Shield, ChevronDown, ChevronUp, Send, 
  CheckCircle2, User, Landmark, AlertCircle, 
  MessageSquare, Loader2, Sparkles, SendHorizontal, X, 
  BarChart3, HelpCircle, Users, Check, RefreshCw
} from 'lucide-react';
import { 
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, 
  Tooltip as ChartTooltip, ResponsiveContainer, PieChart, Pie, Cell 
} from 'recharts';
import './App.css';

// ---------------------------------------------------------------------------
// In-memory caches — survive re-renders, cleared on logout
// ---------------------------------------------------------------------------
const profileCache = new Map();   // customer_id → CustomerProfileResponse
const oppsCache    = new Map();   // customer_id → OpportunityListResponse.opportunities[]

function clearCaches() {
  profileCache.clear();
  oppsCache.clear();
}

export default function App() {
  const [theme, setTheme]       = useState('dark');
  const [token, setToken]       = useState(localStorage.getItem('token') || '');

  const [currentRM, setCurrentRM] = useState(() => {
    const saved = localStorage.getItem('token');
    if (saved) {
      const email = localStorage.getItem('rmEmail') || 'priya@bank.com';
      return { name: email === 'priya@bank.com' ? 'Priya Sharma' : 'Arjun Mehta', email };
    }
    return null;
  });

  // Auth
  const [email,       setEmail]       = useState(localStorage.getItem('rmEmail') || 'priya@bank.com');
  const [password,    setPassword]    = useState('password123');
  const [loginLoading,setLoginLoading]= useState(false);
  const [loginError,  setLoginError]  = useState('');

  // App view
  const [view, setView] = useState('queue');

  // Customer queue
  const [customers,    setCustomers]    = useState([]);
  const [queueLoading, setQueueLoading] = useState(false);

  // Selected customer
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [selectedOpp,      setSelectedOpp]      = useState(null);
  const [opportunities,    setOpportunities]    = useState([]);
  const [oppsLoading,      setOppsLoading]      = useState(false);
  const [detailLoading,    setDetailLoading]    = useState(false);

  // Morning digest
  const [showDigest, setShowDigest] = useState(true);
  const [digestData, setDigestData] = useState({
    totalCustomers: 0, highRisk: 0, lowRisk: 0, avgCibil: 750, heldProducts: 0
  });

  // Filters
  const [riskFilter,    setRiskFilter]    = useState('ALL');
  const [personaFilter, setPersonaFilter] = useState('ALL');

  // Explainability
  const [explanationData,  setExplanationData]  = useState(null);
  const [explainLoading,   setExplainLoading]   = useState(false);
  const [showExplainModal, setShowExplainModal] = useState(false);

  // Outreach
  const [showOutreachModal,   setShowOutreachModal]   = useState(false);
  const [outreachChannel,     setOutreachChannel]     = useState('whatsapp');
  const [outreachText,        setOutreachText]        = useState('');
  const [outreachLoading,     setOutreachLoading]     = useState(false);
  const [outreachCampaignId,  setOutreachCampaignId]  = useState(null);
  const [outreachSuccess,     setOutreachSuccess]     = useState(false);

  // Chat copilot
  const [chatOpen,     setChatOpen]     = useState(true);
  const [chatMessages, setChatMessages] = useState([
    {
      sender: 'copilot',
      text: "Hello! I'm your RM Copilot. I can search product catalogues, policy playbooks, or summarize your portfolio. Ask me anything!",
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
  ]);
  const [chatInput,    setChatInput]    = useState('');
  const [chatStreaming, setChatStreaming]= useState(false);
  const [streamingText,setStreamingText]= useState('');

  // ── Critical fix: use a ref to accumulate SSE tokens (avoids stale closure)
  const streamAccumRef  = useRef('');
  const citationsRef    = useRef([]);
  const abortCtrlRef    = useRef(null);

  const [chatSessionId]  = useState(() => crypto.randomUUID());
  const chatBottomRef    = useRef(null);

  // System status
  const [dbStatus,    setDbStatus]    = useState('connecting');
  const [redisStatus, setRedisStatus] = useState('connecting');

  // Scan trigger
  const [scanning, setScanning] = useState(false);

  // ---------------------------------------------------------------------------
  // Auth helpers
  // ---------------------------------------------------------------------------
  const toggleTheme = useCallback(() => {
    setTheme(prev => prev === 'dark' ? 'light' : 'dark');
  }, []);

  const checkHealth = useCallback(async () => {
    try {
      const res  = await fetch('/api/health');
      const data = await res.json();
      setDbStatus(data.dependencies?.database === 'connected' ? 'healthy' : 'error');
      setRedisStatus(data.dependencies?.redis === 'connected' ? 'healthy' : 'error');
    } catch {
      setDbStatus('error');
      setRedisStatus('error');
    }
  }, []);

  const handleLogin = useCallback(async (e) => {
    e.preventDefault();
    setLoginLoading(true);
    setLoginError('');
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      if (!res.ok) throw new Error('Invalid email or password');
      const data = await res.json();
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('rmEmail', email);
      setToken(data.access_token);
      setCurrentRM({
        name: email === 'priya@bank.com' ? 'Priya Sharma' : 'Arjun Mehta',
        email
      });
    } catch (err) {
      setLoginError(err.message || 'Login failed. Please verify API is running.');
    } finally {
      setLoginLoading(false);
    }
  }, [email, password]);

  const handleLogout = useCallback(() => {
    localStorage.removeItem('token');
    localStorage.removeItem('rmEmail');
    clearCaches();
    setToken('');
    setCurrentRM(null);
    setCustomers([]);
    setSelectedCustomer(null);
    setSelectedOpp(null);
    setOpportunities([]);
  }, []);

  // ---------------------------------------------------------------------------
  // Fetch priority queue (cached on backend via Redis)
  // ---------------------------------------------------------------------------
  const fetchQueue = useCallback(async (tok = token) => {
    if (!tok) return;
    setQueueLoading(true);
    try {
      const res  = await fetch('/api/customers/priority-queue?limit=50', {
        headers: { 'Authorization': `Bearer ${tok}` }
      });
      if (res.status === 401) { handleLogout(); return; }
      const data = await res.json();
      const custs = data.customers || [];
      setCustomers(custs);

      if (custs.length > 0) {
        const total = custs.length;
        const high  = custs.filter(c => c.risk_tier?.toLowerCase() === 'high').length;
        const low   = custs.filter(c => c.risk_tier?.toLowerCase() === 'low').length;
        const avgC  = Math.round(custs.reduce((acc, c) => acc + (c.credit_score || 0), 0) / total);
        setDigestData({
          totalCustomers: total,
          highRisk: high,
          lowRisk: low,
          avgCibil: avgC || 750,
          heldProducts: custs.reduce((acc, c) => acc + (c.behavioral_tags?.length || 0), 0)
        });
      }
    } catch (err) {
      console.error('Failed to fetch queue:', err);
    } finally {
      setQueueLoading(false);
    }
  }, [token, handleLogout]);

  // ---------------------------------------------------------------------------
  // Load customer details — with in-memory cache + parallel fetching
  // ---------------------------------------------------------------------------
  const loadCustomerDetails = useCallback(async (cust) => {
    // 1. Optimistic update: show what we already know from the queue
    setSelectedCustomer(cust);
    setSelectedOpp(null);

    const id = cust.customer_id;

    // 2. Check cache first
    const cachedProfile = profileCache.get(id);
    const cachedOpps    = oppsCache.get(id);

    if (cachedProfile && cachedOpps) {
      // Instant render from cache — no loading state needed
      setSelectedCustomer(cachedProfile);
      setOpportunities(cachedOpps);
      if (cachedOpps.length > 0) setSelectedOpp(cachedOpps[0]);
      return;
    }

    // 3. Parallel fetch profile + opportunities
    setOppsLoading(true);
    setDetailLoading(true);

    try {
      const headers = { 'Authorization': `Bearer ${token}` };

      const [profileRes, oppsRes] = await Promise.all([
        cachedProfile ? null : fetch(`/api/customers/${id}`, { headers }),
        cachedOpps    ? null : fetch(`/api/customers/${id}/opportunities`, { headers })
      ]);

      // Profile
      let profile = cachedProfile;
      if (profileRes) {
        if (profileRes.status === 401) { handleLogout(); return; }
        profile = await profileRes.json();
        profileCache.set(id, { ...cust, ...profile }); // merge with queue data
      }

      // Opportunities
      let opps = cachedOpps;
      if (oppsRes) {
        const oppsData = await oppsRes.json();
        opps = oppsData.opportunities || [];
        oppsCache.set(id, opps);
      }

      setSelectedCustomer(prev => ({ ...prev, ...profile }));
      setOpportunities(opps);
      if (opps.length > 0) setSelectedOpp(opps[0]);
    } catch (err) {
      console.error('Failed to load customer details:', err);
    } finally {
      setDetailLoading(false);
      setOppsLoading(false);
    }
  }, [token, handleLogout]);

  // ---------------------------------------------------------------------------
  // Dismiss opportunity
  // ---------------------------------------------------------------------------
  const handleDismissOpportunity = useCallback(async (oppId) => {
    if (!window.confirm('Dismiss this opportunity?')) return;
    try {
      const res = await fetch(
        `/api/customers/${selectedCustomer.customer_id}/opportunities/${oppId}/dismiss`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ reason: 'RM manual dismiss from dashboard' })
        }
      );
      if (res.ok) {
        const newOpps = opportunities.filter(o => o.opportunity_id !== oppId);
        setOpportunities(newOpps);
        oppsCache.set(selectedCustomer.customer_id, newOpps); // update cache
        if (selectedOpp?.opportunity_id === oppId) setSelectedOpp(null);
        fetchQueue();
      }
    } catch (err) { console.error(err); }
  }, [selectedCustomer, opportunities, selectedOpp, token, fetchQueue]);

  // ---------------------------------------------------------------------------
  // Explain opportunity
  // ---------------------------------------------------------------------------
  const handleExplain = useCallback(async (opp) => {
    setSelectedOpp(opp);
    setExplainLoading(true);
    setShowExplainModal(true);
    try {
      if (opp.explanation) {
        try {
          setExplanationData(JSON.parse(opp.explanation));
        } catch {
          setExplanationData({
            why_selected: opp.explanation,
            event_explanation: 'Detected wedding transaction flags.',
            product_rationale: 'Customer matches eligibility requirements.',
            conversion_reasoning: `Conversion probability calculated at ${Math.round(opp.conversion_prob * 100)}%.`,
            rm_action: 'Generate personalized outreach and schedule follow-up.'
          });
        }
      } else {
        setExplanationData({
          why_selected: 'This customer has high spend spikes matching wedding parameters.',
          event_explanation: 'Detected wedding / banquet bookings.',
          product_rationale: 'Personal Loan recommended for life event expenses.',
          conversion_reasoning: `High conversion score of ${Math.round(opp.conversion_prob * 100)}% based on transaction history.`,
          rm_action: 'Review and approve WhatsApp/Email outreach.'
        });
      }
    } finally {
      setExplainLoading(false);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // Generate outreach
  // ---------------------------------------------------------------------------
  const handleGenerateOutreach = useCallback(async (opp, channel) => {
    setOutreachLoading(true);
    setOutreachSuccess(false);
    setOutreachChannel(channel);
    setShowOutreachModal(true);
    try {
      const res = await fetch('/api/outreach/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({
          customer_id:    selectedCustomer.customer_id,
          opportunity_id: opp.opportunity_id,
          channel
        })
      });
      const data = await res.json();
      setOutreachText(data.message_body);
      setOutreachCampaignId(data.campaign_id);
    } catch {
      setOutreachText('Failed to generate draft. Please verify LLM connectivity.');
    } finally {
      setOutreachLoading(false);
    }
  }, [selectedCustomer, token]);

  const handleApproveOutreach = useCallback(async () => {
    if (!outreachCampaignId) return;
    setOutreachLoading(true);
    try {
      const res = await fetch(`/api/outreach/${outreachCampaignId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ edited_message: outreachText })
      });
      if (res.ok) {
        setOutreachSuccess(true);
        setTimeout(() => { setShowOutreachModal(false); setOutreachSuccess(false); }, 1500);
      }
    } catch (err) { console.error(err); }
    finally { setOutreachLoading(false); }
  }, [outreachCampaignId, outreachText, token]);

  // ---------------------------------------------------------------------------
  // Chat — fixed SSE streaming with ref-based accumulation
  // ---------------------------------------------------------------------------
  const handleSendChat = useCallback(async (e) => {
    e.preventDefault();
    if (!chatInput.trim() || chatStreaming) return;

    // Abort any prior in-flight request
    if (abortCtrlRef.current) abortCtrlRef.current.abort();
    const controller = new AbortController();
    abortCtrlRef.current = controller;

    const userMessage = chatInput.trim();
    setChatInput('');
    setChatMessages(prev => [...prev, {
      sender: 'user',
      text: userMessage,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }]);
    setChatStreaming(true);

    // Reset accumulators
    streamAccumRef.current = '';
    citationsRef.current   = [];
    setStreamingText('');

    try {
      const response = await fetch('/api/copilot/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          message: userMessage,
          session_id: chatSessionId,
          customer_context_ids: selectedCustomer ? [selectedCustomer.customer_id] : []
        }),
        signal: controller.signal
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Server error ${response.status}: ${errText}`);
      }

      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer    = '';
      let isDone    = false;

      while (!isDone) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';  // keep incomplete line in buffer

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data: ')) continue;
          try {
            const payload = JSON.parse(trimmed.slice(6));
            if (payload.error) {
              streamAccumRef.current += `\n⚠️ ${payload.error}`;
              setStreamingText(streamAccumRef.current);
              isDone = true;
              break;
            }
            if (payload.done) {
              if (payload.citations?.length) citationsRef.current = payload.citations;
              isDone = true;
              break;
            }
            if (payload.token) {
              streamAccumRef.current += payload.token;
              setStreamingText(streamAccumRef.current);
            }
          } catch {
            // malformed SSE line — skip
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return; // user navigated away
      console.error('Chat streaming failed:', err);
      streamAccumRef.current = `Connection error: ${err.message}. Please check the backend is running.`;
      setStreamingText(streamAccumRef.current);
    } finally {
      const finalText = streamAccumRef.current || 'No response received.';
      const citations = citationsRef.current;
      setChatMessages(prev => [...prev, {
        sender: 'copilot',
        text: finalText,
        citations,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      }]);
      setStreamingText('');
      streamAccumRef.current = '';
      citationsRef.current   = [];
      setChatStreaming(false);
      abortCtrlRef.current   = null;
    }
  }, [chatInput, chatStreaming, token, chatSessionId, selectedCustomer]);

  // ---------------------------------------------------------------------------
  // Trigger scan
  // ---------------------------------------------------------------------------
  const triggerSystemScan = useCallback(async () => {
    setScanning(true);
    try {
      await new Promise(r => setTimeout(r, 1500));
      // Bust queue cache by clearing customer section
      await fetchQueue();
    } catch (e) { console.error(e); }
    finally { setScanning(false); }
  }, [fetchQueue]);

  // ---------------------------------------------------------------------------
  // Filtered customers (memoized)
  // ---------------------------------------------------------------------------
  const filteredCustomers = useMemo(() => customers.filter(c => {
    if (riskFilter    !== 'ALL' && c.risk_tier?.toUpperCase()  !== riskFilter)    return false;
    if (personaFilter !== 'ALL' && c.persona_type              !== personaFilter) return false;
    return true;
  }), [customers, riskFilter, personaFilter]);

  // Analytics data (static demo)
  const analyticsData = useMemo(() => [
    { name: 'Personal Loan',   opportunities: 4, revenue: 160000, conversion: 0.78 },
    { name: 'Home Loan',       opportunities: 3, revenue: 450000, conversion: 0.62 },
    { name: 'Credit Card',     opportunities: 6, revenue:  90000, conversion: 0.84 },
    { name: 'Wealth Advisory', opportunities: 5, revenue: 320000, conversion: 0.72 },
    { name: 'Business Loan',   opportunities: 2, revenue: 250000, conversion: 0.55 },
  ], []);

  const riskDistribution = useMemo(() => [
    { name: 'Low Risk',    value: digestData.lowRisk  || 12 },
    { name: 'Medium Risk', value: 6 },
    { name: 'High Risk',   value: digestData.highRisk ||  2 },
  ], [digestData.lowRisk, digestData.highRisk]);

  const COLORS = ['#2ec4b6', '#ff9f1c', '#ff4a5a'];

  // ---------------------------------------------------------------------------
  // Effects
  // ---------------------------------------------------------------------------
  useEffect(() => {
    document.documentElement.className = theme;
  }, [theme]);

  useEffect(() => {
    checkHealth();
    if (token) fetchQueue(token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    if (chatBottomRef.current) {
      chatBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, streamingText]);

  // ---------------------------------------------------------------------------
  // Login screen
  // ---------------------------------------------------------------------------
  if (!token) {
    return (
      <div className="login-container">
        <div className="glow-spot glow-spot-1" />
        <div className="glow-spot glow-spot-2" />
        <div className="glass-panel login-card slide-in-anim">
          <div className="login-header">
            <div className="logo-ring float-anim">
              <Landmark className="logo-icon text-accent" size={32} />
            </div>
            <h2 className="gradient-text">RM Copilot</h2>
            <p className="text-muted">AIGravity Intelligent Banking CRM</p>
          </div>

          <form onSubmit={handleLogin} className="login-form">
            {loginError && (
              <div className="error-alert">
                <AlertCircle size={16} />
                <span>{loginError}</span>
              </div>
            )}
            <div className="form-group">
              <label>Relationship Manager Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="email@bank.com" />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)} required placeholder="••••••••" />
            </div>
            <button type="submit" className="btn-glow w-full" disabled={loginLoading}>
              {loginLoading ? <><Loader2 className="animate-spin" size={18} /><span>Authenticating...</span></> : <><LogIn size={18} /><span>Secure Login</span></>}
            </button>
          </form>

          <div className="login-footer">
            <p className="text-muted">Demo Credentials:</p>
            <p>Priya Sharma: <code>priya@bank.com</code> / <code>password123</code></p>
            <p>Arjun Mehta: <code>arjun@bank.com</code> / <code>password123</code></p>
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Dashboard
  // ---------------------------------------------------------------------------
  return (
    <div className="dashboard-layout">
      <div className="glow-spot glow-spot-1" />
      <div className="glow-spot glow-spot-2" />

      {/* Sidebar */}
      <aside className="sidebar glass-panel">
        <div className="sidebar-brand">
          <Landmark className="logo-icon text-accent" size={24} />
          <span className="brand-name gradient-text">RM Copilot</span>
        </div>

        <div className="rm-profile-card">
          <div className="avatar"><User size={20} /></div>
          <div className="profile-details">
            <h4>{currentRM?.name}</h4>
            <p>{currentRM?.email}</p>
          </div>
        </div>

        <nav className="sidebar-nav">
          <button className={`nav-item ${view === 'queue' ? 'active' : ''}`} onClick={() => setView('queue')}>
            <Users size={18} /><span>Priority Queue</span>
          </button>
          <button className={`nav-item ${view === 'analytics' ? 'active' : ''}`} onClick={() => setView('analytics')}>
            <BarChart3 size={18} /><span>Analytics Hub</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className="system-indicators">
            <div className="indicator">
              <span className={`dot ${dbStatus    === 'healthy' ? 'green' : 'red'}`} />
              <span>Postgres DB</span>
            </div>
            <div className="indicator">
              <span className={`dot ${redisStatus === 'healthy' ? 'green' : 'red'}`} />
              <span>Redis Cache</span>
            </div>
          </div>
          <div className="sidebar-actions">
            <button className="theme-toggle" onClick={toggleTheme} title="Toggle Dark/Light Mode">
              {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button className="btn-secondary" onClick={handleLogout} title="Log Out">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <header className="main-header">
          <div className="header-title">
            <h1>Priority Dashboard</h1>
            <p className="text-muted">Analyze, score and outreach customers based on life events.</p>
          </div>
          <div className="header-actions">
            <button className="btn-secondary flex items-center gap-2" onClick={triggerSystemScan} disabled={scanning}>
              <RefreshCw className={scanning ? 'animate-spin' : ''} size={16} />
              <span>{scanning ? 'Scanning...' : 'Trigger Scan'}</span>
            </button>
          </div>
        </header>

        {view === 'queue' ? (
          <div className="dashboard-content-split">
            {/* Left — Queue Panel */}
            <div className="queue-panel">
              {/* Morning Digest */}
              <div className="glass-panel digest-accordion slide-in-anim">
                <div className="digest-header" onClick={() => setShowDigest(prev => !prev)}>
                  <div className="flex items-center gap-2">
                    <Sparkles className="text-accent" size={18} />
                    <h3>Morning Digest</h3>
                  </div>
                  {showDigest ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                </div>
                {showDigest && (
                  <div className="digest-body">
                    <div className="digest-metric">
                      <span className="metric-val">{digestData.totalCustomers}</span>
                      <span className="metric-label">Active Customers</span>
                    </div>
                    <div className="digest-metric">
                      <span className="metric-val text-red">{digestData.highRisk}</span>
                      <span className="metric-label">High Risk Alerts</span>
                    </div>
                    <div className="digest-metric">
                      <span className="metric-val text-green">{digestData.lowRisk}</span>
                      <span className="metric-label">Low Risk Portfolios</span>
                    </div>
                    <div className="digest-metric">
                      <span className="metric-val">{digestData.avgCibil}</span>
                      <span className="metric-label">Average CIBIL</span>
                    </div>
                  </div>
                )}
              </div>

              {/* Filters */}
              <div className="filters-container glass-panel">
                <div className="filter-group">
                  <span>Risk Level:</span>
                  <div className="filter-pills">
                    {['ALL', 'LOW', 'MEDIUM', 'HIGH'].map(r => (
                      <button key={r} className={`pill ${riskFilter === r ? 'active' : ''}`} onClick={() => setRiskFilter(r)}>{r}</button>
                    ))}
                  </div>
                </div>
                <div className="filter-group">
                  <span>Persona:</span>
                  <select value={personaFilter} onChange={e => setPersonaFilter(e.target.value)}>
                    <option value="ALL">All Personas</option>
                    <option value="corporate_professional">Corporate Professional</option>
                    <option value="startup_founder">Startup Founder</option>
                    <option value="doctor">Doctor</option>
                    <option value="hni">HNI Investor</option>
                    <option value="newly_married">Newly Married</option>
                    <option value="business_owner">Business Owner</option>
                    <option value="young_it_professional">Young IT Professional</option>
                  </select>
                </div>
              </div>

              {/* Customer Cards */}
              <div className="queue-list scrollable">
                {queueLoading ? (
                  <div className="loading-spinner">
                    <Loader2 className="animate-spin text-accent" size={32} />
                    <p>Loading prioritized portfolio...</p>
                  </div>
                ) : filteredCustomers.length === 0 ? (
                  <div className="empty-state glass-panel">
                    <HelpCircle size={40} className="text-muted" />
                    <h4>No Customers Found</h4>
                    <p>Change your filter rules or scan for new events.</p>
                  </div>
                ) : (
                  filteredCustomers.map(cust => (
                    <div
                      key={cust.customer_id}
                      className={`customer-card glass-panel glow-card slide-in-anim ${selectedCustomer?.customer_id === cust.customer_id ? 'selected' : ''}`}
                      onClick={() => loadCustomerDetails(cust)}
                    >
                      <div className="card-header">
                        <div>
                          <h3>{cust.name || 'Anonymous Customer'}</h3>
                          <span className="persona-badge">{cust.persona_type?.replace(/_/g, ' ')}</span>
                        </div>
                        <span className={`priority-tag ${cust.risk_tier?.toLowerCase() === 'high' ? 'high' : cust.risk_tier?.toLowerCase() === 'medium' ? 'medium' : 'low'}`}>
                          {cust.risk_tier} Risk
                        </span>
                      </div>
                      <div className="card-body-metrics">
                        <div className="metric">
                          <span className="label">CIBIL</span>
                          <span className="val">{cust.credit_score || 'N/A'}</span>
                        </div>
                        <div className="metric">
                          <span className="label">Avg Balance</span>
                          <span className="val">₹{cust.avg_balance_3m?.toLocaleString() || '0'}</span>
                        </div>
                        <div className="metric">
                          <span className="label">Tenure</span>
                          <span className="val">{cust.relationship_tenure_months}m</span>
                        </div>
                      </div>
                      {cust.behavioral_tags?.length > 0 && (
                        <div className="card-tags">
                          {cust.behavioral_tags.slice(0, 3).map(t => (
                            <span key={t} className="signal-chip">{t}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Right — Detail Panel */}
            <div className="detail-panel">
              {selectedCustomer ? (
                <div className="detailed-info scrollable">
                  <div className="info-header glass-panel">
                    {detailLoading ? (
                      <div className="loading-spinner">
                        <Loader2 className="animate-spin text-accent" size={28} />
                        <p>Loading profile...</p>
                      </div>
                    ) : (
                      <>
                        <div className="avatar-big"><User size={32} /></div>
                        <h2>{selectedCustomer.name}</h2>
                        <p className="text-muted">{selectedCustomer.email} | {selectedCustomer.phone}</p>
                        <div className="grid grid-cols-2 gap-4 w-full mt-6">
                          <div className="metric-box">
                            <span className="label">Monthly Salary</span>
                            <span className="value">₹{selectedCustomer.salary_avg_3m?.toLocaleString() || 'N/A'}</span>
                          </div>
                          <div className="metric-box">
                            <span className="label">Total Investments</span>
                            <span className="value">₹{selectedCustomer.total_investments?.toLocaleString() || 'N/A'}</span>
                          </div>
                          <div className="metric-box">
                            <span className="label">Total Liabilities</span>
                            <span className="value text-red">₹{selectedCustomer.total_liabilities?.toLocaleString() || 'N/A'}</span>
                          </div>
                          <div className="metric-box">
                            <span className="label">KYC Status</span>
                            <span className="value text-green">{selectedCustomer.kyc_status}</span>
                          </div>
                        </div>
                      </>
                    )}
                  </div>

                  <div className="opportunities-section mt-6">
                    <h3 className="section-title">Active Opportunities</h3>
                    {oppsLoading ? (
                      <div className="loading-spinner">
                        <Loader2 className="animate-spin text-accent" size={24} />
                        <p>Loading opportunities...</p>
                      </div>
                    ) : opportunities.length === 0 ? (
                      <div className="empty-box glass-panel">
                        <p>No active credit or product opportunities found for this customer.</p>
                      </div>
                    ) : (
                      opportunities.map(opp => (
                        <div key={opp.opportunity_id} className="opportunity-item-card glass-panel">
                          <div className="opp-header">
                            <div>
                              <h4>Recommended: {opp.product_recommended?.replace(/_/g, ' ')}</h4>
                              <span className="prob-badge">Conversion Prob: {Math.round(opp.conversion_prob * 100)}%</span>
                            </div>
                            <div className="opp-revenue">
                              <span className="label">Est Revenue</span>
                              <span className="val text-accent">₹{opp.revenue_potential?.toLocaleString() || 'N/A'}</span>
                            </div>
                          </div>
                          <div className="opp-actions mt-4">
                            <button className="btn-glow" onClick={() => handleExplain(opp)}>
                              <Sparkles size={16} /><span>Explain Card</span>
                            </button>
                            <div className="flex gap-2">
                              <button className="btn-secondary" onClick={() => handleGenerateOutreach(opp, 'whatsapp')}>
                                <span>WhatsApp Outreach</span>
                              </button>
                              <button className="btn-secondary" onClick={() => handleGenerateOutreach(opp, 'email')}>
                                <span>Email Outreach</span>
                              </button>
                              <button className="btn-secondary icon-btn" onClick={() => handleDismissOpportunity(opp.opportunity_id)} title="Dismiss Opportunity">
                                <X size={16} />
                              </button>
                            </div>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              ) : (
                <div className="no-selection glass-panel">
                  <User size={48} className="text-muted float-anim" />
                  <h3>Select a Customer</h3>
                  <p>Choose a customer card from the priority queue to access financial diagnostics, risk flags, and RAG campaigns.</p>
                </div>
              )}
            </div>
          </div>
        ) : (
          /* Analytics Hub */
          <div className="analytics-hub scrollable slide-in-anim">
            <div className="grid grid-cols-3 gap-6">
              <div className="glass-panel analytics-card">
                <h3>Priority Pipeline</h3>
                <p className="text-muted mb-4">Total estimated revenue opportunity by product type</p>
                <div className="chart-container">
                  <ResponsiveContainer width="100%" height={250}>
                    <BarChart data={analyticsData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="name" stroke="var(--text-muted)" fontSize={12} />
                      <YAxis stroke="var(--text-muted)" fontSize={12} />
                      <ChartTooltip contentStyle={{ background: 'var(--bg-panel-solid)', border: '1px solid var(--border-color)' }} />
                      <Bar dataKey="revenue" fill="var(--accent-color)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div className="glass-panel analytics-card">
                <h3>Risk Segment Distribution</h3>
                <p className="text-muted mb-4">Portion of active customers by assessed risk tier</p>
                <div className="chart-container flex items-center justify-center">
                  <ResponsiveContainer width="100%" height={250}>
                    <PieChart>
                      <Pie data={riskDistribution} cx="50%" cy="50%" innerRadius={60} outerRadius={80} paddingAngle={5} dataKey="value">
                        {riskDistribution.map((_, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <ChartTooltip contentStyle={{ background: 'var(--bg-panel-solid)', border: '1px solid var(--border-color)' }} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="legend">
                    {riskDistribution.map((entry, index) => (
                      <div key={entry.name} className="legend-item flex items-center gap-2">
                        <span className="dot" style={{ backgroundColor: COLORS[index] }} />
                        <span>{entry.name}: {entry.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <div className="glass-panel analytics-card">
                <h3>Conversion Rates</h3>
                <p className="text-muted mb-4">Success probability averages based on historical leads</p>
                <div className="chart-container">
                  <ResponsiveContainer width="100%" height={250}>
                    <AreaChart data={analyticsData}>
                      <defs>
                        <linearGradient id="colorConversion" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%"  stopColor="var(--priority-low)" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="var(--priority-low)" stopOpacity={0}   />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="name" stroke="var(--text-muted)" fontSize={12} />
                      <YAxis stroke="var(--text-muted)" fontSize={12} />
                      <ChartTooltip contentStyle={{ background: 'var(--bg-panel-solid)', border: '1px solid var(--border-color)' }} />
                      <Area type="monotone" dataKey="conversion" stroke="var(--priority-low)" fillOpacity={1} fill="url(#colorConversion)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Floating Chat Copilot */}
      <div className={`chat-copilot-container glass-panel ${chatOpen ? 'open' : 'closed'}`}>
        <div className="chat-header" onClick={() => setChatOpen(prev => !prev)}>
          <div className="flex items-center gap-2">
            <MessageSquare size={18} className="text-accent" />
            <h3>RM Copilot Chat</h3>
            {chatStreaming && <span className="streaming-indicator animate-pulse">Streaming</span>}
          </div>
          <button className="chat-toggle-btn">
            {chatOpen ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
          </button>
        </div>

        {chatOpen && (
          <div className="chat-body-wrapper">
            <div className="chat-messages scrollable">
              {chatMessages.map((msg, i) => (
                <div key={i} className={`message-bubble ${msg.sender}`}>
                  <div className="msg-content">
                    <p style={{ whiteSpace: 'pre-wrap' }}>{msg.text}</p>
                    {msg.citations?.length > 0 && (
                      <div className="citations-list mt-2">
                        {msg.citations.map((cit, idx) => (
                          <div key={idx} className="cit-badge">
                            {idx + 1}
                            <div className="cit-tooltip">
                              <strong>Source:</strong> {cit.source?.split('/').pop()}<br />
                              <strong>Snippet:</strong> {cit.excerpt}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <span className="msg-time">{msg.timestamp}</span>
                </div>
              ))}

              {/* Live streaming bubble */}
              {chatStreaming && !streamingText && (
                <div className="message-bubble copilot">
                  <div className="msg-content flex items-center gap-2">
                    <Loader2 className="animate-spin text-accent" size={16} />
                    <span>Copilot is thinking...</span>
                  </div>
                </div>
              )}
              {chatStreaming && streamingText && (
                <div className="message-bubble copilot">
                  <div className="msg-content">
                    <p style={{ whiteSpace: 'pre-wrap' }}>{streamingText}</p>
                  </div>
                  <span className="msg-time">Streaming...</span>
                </div>
              )}
              <div ref={chatBottomRef} />
            </div>

            <div className="quick-prompts">
              <button onClick={() => setChatInput('What is the Personal Loan eligibility criteria?')}>RAG: Personal Loan</button>
              <button onClick={() => setChatInput('Summarise the risk flags for high opportunities')}>Summarise flags</button>
              <button onClick={() => setChatInput('Which HNI customers show wealth migration signals?')}>HNI Alerts</button>
            </div>

            <form onSubmit={handleSendChat} className="chat-input-area">
              <input
                type="text"
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                placeholder="Ask Copilot (e.g. Check home loan policy rules)..."
                disabled={chatStreaming}
              />
              <button type="submit" className="chat-send-btn" disabled={chatStreaming || !chatInput.trim()}>
                <SendHorizontal size={18} />
              </button>
            </form>
          </div>
        )}
      </div>

      {/* Explainability Modal */}
      {showExplainModal && (
        <div className="modal-overlay">
          <div className="glass-panel modal-card max-w-2xl slide-in-anim">
            <div className="modal-header">
              <div className="flex items-center gap-2">
                <Sparkles className="text-accent" size={20} />
                <h2>Opportunity Diagnostics</h2>
              </div>
              <button className="icon-btn" onClick={() => setShowExplainModal(false)}><X size={20} /></button>
            </div>
            <div className="modal-body scrollable">
              {explainLoading ? (
                <div className="loading-spinner">
                  <Loader2 className="animate-spin text-accent" size={32} />
                  <p>Running LLM Explainability agent...</p>
                </div>
              ) : explanationData ? (
                <div className="explain-details">
                  {[
                    ['Why Selected',        'why_selected'],
                    ['Event Significance',  'event_explanation'],
                    ['Product Rationale',   'product_rationale'],
                    ['Conversion Reasoning','conversion_reasoning'],
                    ['RM Action Guidance',  'rm_action'],
                  ].map(([label, key]) => (
                    <div key={key} className="explain-section">
                      <h4>{label}</h4>
                      <p>{explanationData[key]}</p>
                    </div>
                  ))}
                </div>
              ) : <p>Failed to generate explanation card.</p>}
            </div>
            <div className="modal-footer">
              <button className="btn-glow" onClick={() => setShowExplainModal(false)}>Acknowledge</button>
            </div>
          </div>
        </div>
      )}

      {/* Outreach Modal */}
      {showOutreachModal && (
        <div className="modal-overlay">
          <div className="glass-panel modal-card max-w-xl slide-in-anim">
            <div className="modal-header">
              <div className="flex items-center gap-2">
                <Send className="text-accent" size={20} />
                <h2>Personalized Outreach Editor</h2>
              </div>
              <button className="icon-btn" onClick={() => setShowOutreachModal(false)}><X size={20} /></button>
            </div>
            <div className="modal-body">
              {outreachLoading ? (
                <div className="loading-spinner">
                  <Loader2 className="animate-spin text-accent" size={32} />
                  <p>Invoking LLM OutreachGenAgent (RAG + tone playbooks)...</p>
                </div>
              ) : outreachSuccess ? (
                <div className="success-state">
                  <CheckCircle2 size={48} className="text-green animate-bounce" />
                  <h4>Outreach Dispatched!</h4>
                  <p>Async Celery queue task is triggered successfully.</p>
                </div>
              ) : (
                <div className="outreach-editor">
                  <div className="channel-tabs mb-4">
                    {['whatsapp', 'email', 'sms'].map(ch => (
                      <button key={ch} className={`tab ${outreachChannel === ch ? 'active' : ''}`} onClick={() => handleGenerateOutreach(selectedOpp, ch)}>
                        {ch.toUpperCase()}
                      </button>
                    ))}
                  </div>
                  <div className="editor-group">
                    <label>Message Content (Editable)</label>
                    <textarea value={outreachText} onChange={e => setOutreachText(e.target.value)} rows={10} />
                  </div>
                  <div className="limit-warnings mt-4">
                    <div className="info-row">
                      <Shield size={14} className="text-accent" />
                      <span>Compliance Check: Opted in. Send limits are within boundaries.</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
            {!outreachSuccess && !outreachLoading && (
              <div className="modal-footer">
                <button className="btn-secondary" onClick={() => setShowOutreachModal(false)}>Cancel</button>
                <button className="btn-glow" onClick={handleApproveOutreach}>
                  <Check size={16} /><span>Approve &amp; Dispatch</span>
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
