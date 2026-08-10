import {
  Activity, ChartCandlestick, ClipboardList, Command, Gauge, Globe,
  GraduationCap, LayoutDashboard, Newspaper, Settings, ShieldAlert,
  Telescope, TrendingUp, Wallet, type LucideIcon,
} from 'lucide-react'

export type NavItem = { to: string; label: string; icon: LucideIcon }
export type NavGroup = { group: string; items: NavItem[] }

// Data-only navigation authority. Route components remain lazy-owned by App.
export const NAV: NavGroup[] = [
  { group: '', items: [
    { to: '/', label: 'Overview', icon: LayoutDashboard },
    { to: '/command-center', label: 'Command Center', icon: Command },
  ] },
  { group: 'Research', items: [
    { to: '/universe', label: 'Universe Board', icon: Globe },
    { to: '/charts', label: 'Markets', icon: ChartCandlestick },
    { to: '/prediction-markets', label: 'Prediction Markets', icon: TrendingUp },
    { to: '/serenity-lens', label: 'Serenity Lens', icon: Telescope },
    { to: '/news', label: 'News Intel', icon: Newspaper },
  ] },
  { group: 'Decision', items: [
    { to: '/blotter', label: 'Blotter', icon: ClipboardList },
    { to: '/agent-activity', label: 'Agent Activity', icon: Activity },
  ] },
  { group: 'Portfolio', items: [
    { to: '/trades', label: 'Trades & Positions', icon: Wallet },
    { to: '/risk', label: 'Risk & Controls', icon: ShieldAlert },
  ] },
  { group: '', items: [
    { to: '/learning', label: 'Learning', icon: GraduationCap },
    { to: '/usage', label: 'Usage', icon: Gauge },
    { to: '/settings', label: 'Settings', icon: Settings },
  ] },
]
