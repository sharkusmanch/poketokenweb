import type { TabId } from '../types'

interface TabBarProps {
  tab: TabId
  strings: Record<string, string>
  onSelect: (tab: TabId) => void
}

const ICONS: Record<TabId, string> = {
  home: '🏠',
  shop: '🛒',
  bag: '🎒',
  collection: '📖',
  settings: '⚙️',
}

export function TabBar({ tab, strings, onSelect }: TabBarProps) {
  // Every label comes from the engine catalogue, Settings included. It was the
  // one hardcoded English string in the tab bar, which meant a ko/ja/es user
  // read four translated tabs and one English one.
  const tabs: { id: TabId; label: string }[] = [
    { id: 'home', label: strings.home ?? 'Home' },
    { id: 'shop', label: strings.shop ?? 'Shop' },
    { id: 'bag', label: strings.bag ?? 'Bag' },
    { id: 'collection', label: strings.collection ?? 'Collection' },
    { id: 'settings', label: strings.settings ?? 'Settings' },
  ]

  return (
    <nav className="tabbar" role="tablist" aria-label="Sections">
      {tabs.map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="tab"
          className={entry.id === tab ? 'tabbar-btn tabbar-on' : 'tabbar-btn'}
          aria-selected={entry.id === tab}
          onClick={() => onSelect(entry.id)}
        >
          <span className="tabbar-icon" aria-hidden="true">
            {ICONS[entry.id]}
          </span>
          <span className="tabbar-label">{entry.label}</span>
        </button>
      ))}
    </nav>
  )
}
