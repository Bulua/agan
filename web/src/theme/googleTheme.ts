import type { GlobalThemeOverrides } from 'naive-ui'

const FONT_FAMILY =
  "'Roboto', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
const FONT_MONO = "'JetBrains Mono', 'Roboto Mono', Consolas, monospace"

export const googleLightThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontFamily: FONT_FAMILY,
    fontFamilyMono: FONT_MONO,
    primaryColor: '#1A73E8',
    primaryColorHover: '#1765CC',
    primaryColorPressed: '#1557B0',
    primaryColorSuppl: '#1A73E8',
    infoColor: '#1A73E8',
    infoColorHover: '#1765CC',
    successColor: '#1E8E3E',
    successColorHover: '#188038',
    warningColor: '#E37400',
    warningColorHover: '#B06000',
    errorColor: '#D93025',
    errorColorHover: '#C5221F',
    bodyColor: '#F8F9FA',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    tableColor: '#FFFFFF',
    tableHeaderColor: '#F8F9FA',
    tableColorHover: '#F1F3F4',
    textColorBase: '#202124',
    textColor1: '#202124',
    textColor2: '#3C4043',
    textColor3: '#5F6368',
    borderColor: '#DADCE0',
    dividerColor: '#E8EAED',
    inputColor: '#FFFFFF',
    borderRadius: '4px',
    borderRadiusSmall: '2px',
  },
  Button: {
    borderRadiusMedium: '4px',
    borderRadiusSmall: '4px',
    borderRadiusTiny: '2px',
    fontWeight: '500',
  },
  Card: {
    borderRadius: '4px',
    borderColor: '#DADCE0',
  },
  Input: {
    borderRadius: '4px',
  },
  Select: {
    peers: {
      InternalSelection: {
        borderRadius: '4px',
      },
    },
  },
  Tag: {
    borderRadius: '2px',
  },
  DataTable: {
    borderRadius: '4px',
    thColor: '#F8F9FA',
    thTextColor: '#5F6368',
    thFontWeight: '600',
    borderColor: '#E8EAED',
  },
  Drawer: {
    color: '#FFFFFF',
    headerBorderBottom: '1px solid #DADCE0',
    footerBorderTop: '1px solid #DADCE0',
  },
  Tabs: {
    tabTextColorActiveLine: '#1A73E8',
    barColor: '#1A73E8',
    tabFontWeightActive: '500',
  },
}

export const googleDarkThemeOverrides: GlobalThemeOverrides = {
  common: {
    fontFamily: FONT_FAMILY,
    fontFamilyMono: FONT_MONO,
    primaryColor: '#8AB4F8',
    primaryColorHover: '#AECBFA',
    primaryColorPressed: '#669DF6',
    primaryColorSuppl: '#8AB4F8',
    infoColor: '#8AB4F8',
    infoColorHover: '#AECBFA',
    successColor: '#81C995',
    successColorHover: '#A8DAB5',
    warningColor: '#FDD663',
    warningColorHover: '#FDE293',
    errorColor: '#F28B82',
    errorColorHover: '#F6AEA9',
    bodyColor: '#16181B',
    cardColor: '#202124',
    modalColor: '#202124',
    popoverColor: '#282A2E',
    tableColor: '#202124',
    tableHeaderColor: '#282A2E',
    tableColorHover: '#2D3035',
    textColorBase: '#E8EAED',
    textColor1: '#E8EAED',
    textColor2: '#BDC1C6',
    textColor3: '#9AA0A6',
    borderColor: '#3C4043',
    dividerColor: '#2F3237',
    inputColor: '#1A1C1F',
    borderRadius: '4px',
    borderRadiusSmall: '2px',
  },
  Button: {
    borderRadiusMedium: '4px',
    borderRadiusSmall: '4px',
    borderRadiusTiny: '2px',
    fontWeight: '500',
    textColorPrimary: '#202124',
    textColorHoverPrimary: '#202124',
    textColorPressedPrimary: '#202124',
    textColorFocusPrimary: '#202124',
  },
  Card: {
    borderRadius: '4px',
    borderColor: '#3C4043',
  },
  Input: {
    borderRadius: '4px',
  },
  Select: {
    peers: {
      InternalSelection: {
        borderRadius: '4px',
      },
    },
  },
  Tag: {
    borderRadius: '2px',
  },
  DataTable: {
    borderRadius: '4px',
    thColor: '#282A2E',
    thTextColor: '#9AA0A6',
    thFontWeight: '600',
    borderColor: '#2F3237',
  },
  Drawer: {
    color: '#202124',
    headerBorderBottom: '1px solid #3C4043',
    footerBorderTop: '1px solid #3C4043',
  },
  Tabs: {
    tabTextColorActiveLine: '#8AB4F8',
    barColor: '#8AB4F8',
    tabFontWeightActive: '500',
  },
}
