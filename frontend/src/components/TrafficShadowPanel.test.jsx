import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import AppsBoard from './AppsBoard';
import TrafficShadowPanel from './TrafficShadowPanel';

const record = {
  event_id: 'a'.repeat(32),
  observed_at: 1_700_000_000,
  app_id: 'guestbook',
  current_replica_count: 2,
  cpu_percentages: [44, 62],
  node_load_per_core: [0.4, 0.8],
  current_1m: { rps: 12.5, error_ratio: 0.02 },
  current_5m: { rps: 9.25 },
  rule_status: 'normal_increase',
  data_quality: 'complete',
  actual_fleet_decision: 'scale_out',
  shadow_decision: 'scale_out_candidate',
  decisions_match: false,
  rationale: ['traffic_increase_with_resource_pressure'],
};

function render(state) {
  return renderToStaticMarkup(<TrafficShadowPanel state={state} />);
}

describe('TrafficShadowPanel', () => {
  it('renders metrics and clearly marks differing actual and shadow decisions', () => {
    const html = render({ status: 'ready', enabled: true, records: [record] });
    expect(html).toContain('guestbook');
    expect(html).toContain('12.50');
    expect(html).toContain('9.25');
    expect(html).toContain('정상 수요 증가');
    expect(html).toContain('실제: 확장');
    expect(html).toContain('Shadow: 확장 후보');
    expect(html).toContain('판단 불일치');
    expect(html).toContain('관측 전용');
    expect(html).toContain('판단은 분석·권고에만 사용');
  });

  it('shows disabled and empty states without reporting an error', () => {
    expect(render({ status: 'ready', enabled: false, records: [] })).toContain('비활성화');
    expect(render({ status: 'ready', enabled: true, records: [] })).toContain('아직 비교 결과가 없습니다');
    expect(render({ status: 'loading', records: [] })).toContain('불러오는 중');
  });

  it('distinguishes authentication and server errors', () => {
    const auth = render({ status: 'error', error: { kind: 'auth' }, records: [] });
    const server = render({ status: 'error', error: { kind: 'server' }, records: [] });
    expect(auth).toContain('인증 오류');
    expect(server).toContain('불러오지 못했습니다');
    expect(server).not.toContain('인증 오류');
  });

  it('renders uncertain and insufficient-data classifications distinctly', () => {
    const uncertain = render({
      status: 'ready',
      enabled: true,
      records: [
        { ...record, event_id: 'b'.repeat(32), rule_status: 'uncertain' },
        { ...record, event_id: 'c'.repeat(32), rule_status: 'insufficient_data' },
      ],
    });
    expect(uncertain).toContain('판단 불확실');
    expect(uncertain).toContain('데이터 부족');
  });

  it('marks insufficient-data comparisons as not comparable', () => {
    const html = render({
      status: 'ready',
      enabled: true,
      records: [{ ...record, rule_status: 'insufficient_data', shadow_decision: 'insufficient_data', decisions_match: null }],
    });

    expect(html).toContain('비교 불가');
    expect(html).not.toContain('판단 불일치');
    expect(html).toContain('data-match="unknown"');
  });

  it('keeps the latest Fleet node panel beside Shadow observations', () => {
    const html = renderToStaticMarkup(
      <AppsBoard
        projects={[]}
        projectsState={{ status: 'ready' }}
        fleet={{ nodes: [], apps: [] }}
        trafficShadow={{ status: 'ready', enabled: true, records: [] }}
      />,
    );
    expect(html).toContain('노드 상태');
    expect(html).toContain('Fleet Shadow Mode');
  });
});
