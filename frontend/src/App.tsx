import React, { useEffect, useState } from 'react';
import { Button, ConfigProvider, Layout, Menu, Typography, type MenuProps } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import {
  ApiOutlined,
  AppstoreOutlined,
  ControlOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LinkOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PictureOutlined,
  PlaySquareOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { type AppConfig, fetchConfig } from './api/client';
import { WorkbenchPage, type WorkbenchPreset } from './pages/WorkbenchPage';
import { ResultsPage } from './pages/ResultsPage';
import { type AnalyzerDraft, ConfigPage } from './pages/ConfigPage';
import { AssetsPage } from './pages/AssetsPage';
import { BenchmarkPage } from './pages/BenchmarkPage';

import './App.css';

const { Sider, Content } = Layout;
const { Text } = Typography;

type NavKey =
  | 'workbench-text'
  | 'benchmark'
  | 'workbench-video'
  | 'assets-aigc'
  | 'assets-real'
  | 'ecloud-assets-aigc'
  | 'ecloud-assets-real'
  | 'workbench-image'
  | 'results'
  | 'config-general'
  | 'config-models'
  | 'config-assets';

type SubMenuKey = 'group-text' | 'group-video' | 'group-other' | 'group-config';

const SUBMENU_BY_TAB: Partial<Record<NavKey, SubMenuKey>> = {
  'workbench-text': 'group-text',
  benchmark: 'group-text',
  'workbench-video': 'group-video',
  'assets-aigc': 'group-video',
  'assets-real': 'group-video',
  'ecloud-assets-aigc': 'group-video',
  'ecloud-assets-real': 'group-video',
  'workbench-image': 'group-other',
  'config-general': 'group-config',
  'config-models': 'group-config',
  'config-assets': 'group-config',
};

const pageLabels: Record<NavKey, string> = {
  'workbench-text': '文字模型测试 / 测试工作台',
  benchmark: '文字模型测试 / LLM 压测与自检',
  'workbench-video': '视频模型测试 / 视频测试工作台',
  'assets-aigc': '视频模型测试 / 虚拟人像（AIGC）',
  'assets-real': '视频模型测试 / 真人人像',
  'ecloud-assets-aigc': '视频模型测试 / 【移动云】虚拟人像（aigc）',
  'ecloud-assets-real': '视频模型测试 / 【移动云】真人人像',
  'workbench-image': '其他模型 / Images 图片生成',
  results: '结果解读',
  'config-general': '配置管理 / 通用配置',
  'config-models': '配置管理 / 模型调用配置',
  'config-assets': '配置管理 / 素材库链接配置',
};

// 按模型类型与配置模块组织可折叠二级菜单（SubMenu）。
const navigationItems: MenuProps['items'] = [
  {
    key: 'group-text',
    icon: <MessageOutlined />,
    label: '文字模型测试',
    children: [
      { key: 'workbench-text', icon: <ExperimentOutlined />, label: '测试工作台' },
      { key: 'benchmark', icon: <ThunderboltOutlined />, label: 'LLM 压测与自检' },
    ],
  },
  {
    key: 'group-video',
    icon: <VideoCameraOutlined />,
    label: '视频模型测试',
    children: [
      { key: 'workbench-video', icon: <PlaySquareOutlined />, label: '视频测试工作台' },
      { key: 'assets-aigc', icon: <PictureOutlined />, label: '虚拟人像（AIGC）' },
      { key: 'assets-real', icon: <UserOutlined />, label: '真人人像' },
      { key: 'ecloud-assets-aigc', icon: <PictureOutlined />, label: '【移动云】虚拟人像（aigc）' },
      { key: 'ecloud-assets-real', icon: <UserOutlined />, label: '【移动云】真人人像' },
    ],
  },
  {
    key: 'group-other',
    icon: <AppstoreOutlined />,
    label: '其他模型',
    children: [
      { key: 'workbench-image', icon: <PictureOutlined />, label: 'Images 图片生成' },
    ],
  },
  { key: 'results', icon: <FileSearchOutlined />, label: '结果解读' },
  {
    key: 'group-config',
    icon: <SettingOutlined />,
    label: '配置管理',
    children: [
      { key: 'config-general', icon: <ControlOutlined />, label: '通用配置' },
      { key: 'config-models', icon: <ApiOutlined />, label: '模型调用配置' },
      { key: 'config-assets', icon: <LinkOutlined />, label: '素材库链接配置' },
    ],
  },
];

function resolveWorkbenchTab(apiType?: WorkbenchPreset['api_type']): NavKey {
  if (apiType === 'video' || apiType === 'seedance' || apiType === 'bailian-video') {
    return 'workbench-video';
  }
  if (apiType === 'images/generations') {
    return 'workbench-image';
  }
  return 'workbench-text';
}

export const App: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [openKeys, setOpenKeys] = useState<string[]>([
    'group-text',
    'group-video',
    'group-other',
    'group-config',
  ]);
  const [activeTab, setActiveTab] = useState<NavKey>('workbench-text');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [selectedResult, setSelectedResult] = useState<{
    category: string;
    folder: string;
  } | null>(null);
  const [workbenchPreset, setWorkbenchPreset] = useState<WorkbenchPreset | null>(null);
  const [analyzerDraft, setAnalyzerDraft] = useState<AnalyzerDraft | null>(null);

  const loadConfig = async () => {
    try {
      const data = await fetchConfig();
      setConfig(data);
    } catch {
      // ignore initial error
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleNavigateToResult = (category: string, folder: string) => {
    setSelectedResult({ category, folder });
    setActiveTab('results');
  };

  const handleJumpToWorkbench = (preset: WorkbenchPreset) => {
    const targetTab = resolveWorkbenchTab(preset.api_type);
    const parentKey = SUBMENU_BY_TAB[targetTab];
    if (parentKey) {
      setOpenKeys((prev) => (prev.includes(parentKey) ? prev : [...prev, parentKey]));
    }
    setWorkbenchPreset(preset);
    setActiveTab(targetTab);
  };

  const handleJumpToGeneralWithAnalyzer = (draft: AnalyzerDraft) => {
    setOpenKeys((prev) => (prev.includes('group-config') ? prev : [...prev, 'group-config']));
    setAnalyzerDraft(draft);
    setActiveTab('config-general');
  };

  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff', borderRadius: 8 } }}>
      <Layout className="app-shell">
        <Sider className="app-sidebar" theme="light" width={248} collapsedWidth={64}
          collapsible collapsed={collapsed} onCollapse={setCollapsed} breakpoint="lg" trigger={null}>
          <div className="app-brand">
            <div className="app-brand-mark" aria-hidden="true">B</div>
            {!collapsed && <div><strong>接口诊断工作台</strong><span>BO · LLMEVAL</span></div>}
          </div>
          <nav aria-label="主导航" className="app-navigation">
            <Menu
              mode="inline"
              selectedKeys={[activeTab]}
              {...(collapsed ? {} : { openKeys, onOpenChange: setOpenKeys })}
              onClick={(e) => setActiveTab(e.key as NavKey)}
              items={navigationItems}
            />
          </nav>
          <div className="app-sidebar-footer">
            {!collapsed && <>
              <Text type="secondary" className="app-sidebar-status">
                AI 解读：{config?.analyzer?.configured ? config.analyzer.model : '内置诊断规则'}
              </Text>
              <Text type="secondary" className="app-sidebar-status">已配凭证 {config?.profiles?.length ?? 0} 组</Text>
            </>}
            <Button type="text" block icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              aria-label={collapsed ? '展开导航' : '收起导航'} onClick={() => setCollapsed(!collapsed)}>
              {!collapsed && '收起导航'}
            </Button>
          </div>
        </Sider>
        <Layout className="app-main">
          <Content className="app-content">
            <div className="app-page-context">{pageLabels[activeTab]}</div>
            {activeTab === 'benchmark' && <BenchmarkPage config={config} />}
            {activeTab === 'ecloud-assets-aigc' && <AssetsPage config={config} key="ecloud-AIGC" provider="ecloud" groupType="AIGC" onNavigateToResult={handleNavigateToResult} />}
            {activeTab === 'ecloud-assets-real' && <AssetsPage config={config} key="ecloud-LivenessFace" provider="ecloud" groupType="LivenessFace" onNavigateToResult={handleNavigateToResult} />}
            {activeTab === 'assets-aigc' && <AssetsPage config={config} key="AIGC" groupType="AIGC" onNavigateToResult={handleNavigateToResult} />}
            {activeTab === 'assets-real' && <AssetsPage config={config} key="LivenessFace" groupType="LivenessFace" onNavigateToResult={handleNavigateToResult} />}
            {activeTab === 'workbench-text' && (
              <WorkbenchPage key="text" scope="text" config={config} preset={workbenchPreset} onClearPreset={() => setWorkbenchPreset(null)} onNavigateToResult={handleNavigateToResult} />
            )}
            {activeTab === 'workbench-video' && (
              <WorkbenchPage key="video" scope="video" config={config} preset={workbenchPreset} onClearPreset={() => setWorkbenchPreset(null)} onNavigateToResult={handleNavigateToResult} />
            )}
            {activeTab === 'workbench-image' && (
              <WorkbenchPage key="image" scope="image" config={config} preset={workbenchPreset} onClearPreset={() => setWorkbenchPreset(null)} onNavigateToResult={handleNavigateToResult} />
            )}
            {activeTab === 'results' && (
              <ResultsPage selectedTarget={selectedResult} onSelectTarget={setSelectedResult} onJumpToWorkbench={handleJumpToWorkbench} />
            )}
            {activeTab === 'config-general' && (
              <ConfigPage
                key="config-general"
                section="general"
                analyzerDraft={analyzerDraft}
                onClearAnalyzerDraft={() => setAnalyzerDraft(null)}
                onConfigSaved={setConfig}
              />
            )}
            {activeTab === 'config-models' && (
              <ConfigPage
                key="config-models"
                section="models"
                onJumpToGeneralWithAnalyzer={handleJumpToGeneralWithAnalyzer}
                onConfigSaved={setConfig}
              />
            )}
            {activeTab === 'config-assets' && (
              <ConfigPage
                key="config-assets"
                section="assets"
                onConfigSaved={setConfig}
              />
            )}
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
};

export default App;
