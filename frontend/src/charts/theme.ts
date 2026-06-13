import { ColorType } from 'lightweight-charts'

export const chartTheme = {
  layout: { background: { type: ColorType.Solid, color: '#141a21' }, textColor: '#e6edf3' },
  grid: { vertLines: { color: '#232c36' }, horzLines: { color: '#232c36' } },
  borderColor: '#232c36',                  // --border: price/time scale borders
  upColor: '#2ee6a8',
  downColor: '#ff5d6c',
  lineColor: '#22d3ee',
  areaTopColor: 'rgba(34, 211, 238, 0.30)',   // --accent fade (top)
  areaBottomColor: 'rgba(34, 211, 238, 0.02)', // --accent fade (bottom)
  markerBuy: '#2ee6a8',
  markerSell: '#ff5d6c',
} as const
