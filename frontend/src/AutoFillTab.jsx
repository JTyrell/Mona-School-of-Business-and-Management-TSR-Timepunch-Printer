import { useState } from 'react'

export default function AutoFillTab({ apiBase }) {
  const [file, setFile] = useState(null)
  
  const currentYear = new Date().getFullYear()
  const currentMonth = new Date().getMonth() + 1
  
  const [year, setYear] = useState(currentYear)
  const [month, setMonth] = useState(currentMonth)
  
  const [sheets, setSheets] = useState('Helpdesk Timesheet')
  
  const defaultTimes = { start: '08:00 AM', end: '05:00 PM' }
  const createDefaultSchedule = () => ({
    0: { enabled: true, ...defaultTimes },
    1: { enabled: false, ...defaultTimes },
    2: { enabled: true, ...defaultTimes },
    3: { enabled: false, ...defaultTimes },
    4: { enabled: true, ...defaultTimes },
    5: { enabled: false, ...defaultTimes },
    6: { enabled: false, ...defaultTimes },
  })

  const [standardSchedule, setStandardSchedule] = useState(createDefaultSchedule())
  
  const [midMonthEnabled, setMidMonthEnabled] = useState(false)
  const [midMonthDate, setMidMonthDate] = useState('')
  const [midMonthSchedule, setMidMonthSchedule] = useState(createDefaultSchedule())

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  
  const daysOfWeek = [
    { id: 0, label: 'Monday' },
    { id: 1, label: 'Tuesday' },
    { id: 2, label: 'Wednesday' },
    { id: 3, label: 'Thursday' },
    { id: 4, label: 'Friday' },
    { id: 5, label: 'Saturday' },
    { id: 6, label: 'Sunday' },
  ]

  const updateSchedule = (scheduleState, setScheduleState, dayId, field, value) => {
    setScheduleState(prev => ({
      ...prev,
      [dayId]: {
        ...prev[dayId],
        [field]: value
      }
    }))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!file) {
      setError('Please select an Excel file.')
      return
    }

    setError(null)
    setLoading(true)

    const formData = new FormData()
    formData.append('file', file)
    formData.append('year', year.toString())
    formData.append('month', month.toString())
    formData.append('sheets', sheets)
    
    const standardSchedPayload = {
      ...standardSchedule,
      description: "Helpdesk"
    }
    formData.append('standard_schedule', JSON.stringify(standardSchedPayload))
    
    const midMonthSchedPayload = {
      ...midMonthSchedule,
      enabled: midMonthEnabled,
      start_date: midMonthDate,
      description: "Helpdesk"
    }
    formData.append('mid_month_schedule', JSON.stringify(midMonthSchedPayload))

    try {
      const response = await fetch(`${apiBase}/api/autofill`, { method: 'POST', body: formData })

      if (!response.ok) {
        let errData
        try { errData = await response.json() } catch (_) { throw new Error(`API returned Status ${response.status}`) }
        throw new Error(errData.error || 'Failed to autofill timesheets')
      }

      const blob = await response.blob()
      if (blob.size < 100) throw new Error('Returned file appears empty. Check server logs.')
      
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `Filled_${file.name}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
      
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const renderDayConfig = (scheduleState, setScheduleState) => {
    return daysOfWeek.map(d => {
      const dayData = scheduleState[d.id]
      return (
        <div key={d.id} className="day-config-row">
          <label className="day-checkbox">
            <input 
              type="checkbox" 
              checked={dayData.enabled}
              onChange={(e) => updateSchedule(scheduleState, setScheduleState, d.id, 'enabled', e.target.checked)}
            />
            <span className="day-label-text">{d.label}</span>
          </label>
          
          <div className={`day-time-inputs ${dayData.enabled ? 'active' : ''}`}>
            <input 
              type="text" 
              value={dayData.start}
              onChange={(e) => updateSchedule(scheduleState, setScheduleState, d.id, 'start', e.target.value)}
              placeholder="08:00 AM"
              disabled={!dayData.enabled}
            />
            <span className="time-separator">to</span>
            <input 
              type="text" 
              value={dayData.end}
              onChange={(e) => updateSchedule(scheduleState, setScheduleState, d.id, 'end', e.target.value)}
              placeholder="05:00 PM"
              disabled={!dayData.enabled}
            />
          </div>
        </div>
      )
    })
  }

  return (
    <div className="autofill-tab">
      <p className="subtitle">Automatically populate your blank timesheet with expected worked hours.</p>
      
      {error && <div className="error-banner">{error}</div>}
      
      <form onSubmit={handleSubmit} className="autofill-form">
        <div className="form-group">
          <label>Excel Template File</label>
          <input
            type="file"
            accept=".xlsx, .xls"
            onChange={(e) => setFile(e.target.files[0])}
            required
          />
        </div>
        
        <div className="form-row">
          <div className="form-group half">
            <label>Month</label>
            <select value={month} onChange={(e) => setMonth(parseInt(e.target.value))}>
              {Array.from({length: 12}, (_, i) => i + 1).map(m => (
                <option key={m} value={m}>{new Date(2000, m - 1).toLocaleString('default', { month: 'long' })}</option>
              ))}
            </select>
          </div>
          <div className="form-group half">
            <label>Year</label>
            <input type="number" value={year} onChange={(e) => setYear(parseInt(e.target.value))} required />
          </div>
        </div>

        <div className="form-group">
          <label>Sheets to Fill (comma-separated)</label>
          <input 
            type="text" 
            value={sheets} 
            onChange={(e) => setSheets(e.target.value)} 
            placeholder="e.g. Helpdesk Timesheet, Bloomberg Support Timesheet"
            required
          />
        </div>

        <div className="card-section">
          <h3>Standard Schedule</h3>
          <p className="section-desc">Select working days and specify exact start/end times.</p>
          <div className="day-config-container">
            {renderDayConfig(standardSchedule, setStandardSchedule)}
          </div>
        </div>

        <div className="card-section" style={{ marginTop: '1.5rem' }}>
          <div className="mid-month-header">
            <h3>Mid-Month Update</h3>
            <label className="switch">
              <input type="checkbox" checked={midMonthEnabled} onChange={(e) => setMidMonthEnabled(e.target.checked)} />
              <span className="slider round"></span>
            </label>
          </div>
          
          {midMonthEnabled && (
            <div className="mid-month-content">
              <div className="form-group">
                <label>Effective Start Date</label>
                <input 
                  type="date" 
                  value={midMonthDate} 
                  onChange={(e) => setMidMonthDate(e.target.value)} 
                  required={midMonthEnabled}
                />
              </div>
              <p className="section-desc">Schedule override from the start date onwards.</p>
              <div className="day-config-container">
                {renderDayConfig(midMonthSchedule, setMidMonthSchedule)}
              </div>
            </div>
          )}
        </div>

        <button type="submit" disabled={loading} style={{ marginTop: '1.5rem' }}>
          {loading ? 'Processing...' : 'Auto-Fill Document'}
        </button>
      </form>
    </div>
  )
}
