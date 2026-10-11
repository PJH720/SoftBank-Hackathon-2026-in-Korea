import React from 'react';

const STATUS_LABELS = {
  normal_increase: '정상 수요 증가',
  attack_suspected: '공격 의심 · 검토 필요',
  uncertain: '판단 불확실',
  insufficient_data: '데이터 부족',
};

const ACTUAL_DECISION_LABELS = {
  scale_out: '실제: 확장',
  scale_in: '실제: 축소',
  hold: '실제: 유지',
  blocked: '실제: 확장 제한',
};

const SHADOW_DECISION_LABELS = {
  hold: 'Shadow: 유지',
  scale_out_candidate: 'Shadow: 확장 후보',
  scale_in_candidate: 'Shadow: 축소 후보',
  insufficient_data: 'Shadow: 데이터 부족',
};

const RATIONALE_LABELS = {
  traffic_increase_with_resource_pressure: '트래픽 증가와 자원 부하가 함께 관측됨',
  low_absolute_demand_and_low_resource_use: '요청량과 자원 사용이 모두 낮음',
  attack_suspected_requires_security_review: '공격을 확정하지 않고 보안 검토를 권고함',
  uncertain_requires_more_observation: '추가 관측이 필요함',
  insufficient_recent_sample: '최근 요청 표본이 부족함',
  input_data_gap: '로그 누락 등 데이터 공백이 있음',
  no_shadow_action_threshold_met: 'Shadow 확장·축소 기준에 도달하지 않음',
};

const STATUS_TONES = {
  normal_increase: 'normal',
  attack_suspected: 'warning',
  uncertain: 'uncertain',
  insufficient_data: 'insufficient',
};

const QUALITY_LABELS = {
  complete: '완전',
  degraded: '저하',
  gap_reported: '로그 누락 보고',
};

function formatTime(timestamp) {
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime()) ? '시각 정보 없음' : date.toLocaleString();
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';
}

function IncidentCard({ record }) {
  const avgCpu = record.cpu_percentages?.filter(Number.isFinite);
  const nodeLoads = record.node_load_per_core?.filter(Number.isFinite);
  const cpuText = avgCpu?.length
    ? `${(avgCpu.reduce((total, value) => total + value, 0) / avgCpu.length).toFixed(1)}%`
    : '—';
  const nodeLoadText = nodeLoads?.length
    ? `${Math.max(...nodeLoads).toFixed(2)} / core`
    : '—';
  const rationale = record.rationale?.map((item) => RATIONALE_LABELS[item] || '추가 관측 권고') || [];

  return (
    <article className="traffic-shadow-card" data-tone={STATUS_TONES[record.rule_status] || 'uncertain'}>
      <div className="traffic-shadow-card-head">
        <div>
          <h4>{record.app_id}</h4>
          <p>{formatTime(record.observed_at)}</p>
        </div>
        <span className="traffic-shadow-status">
          {STATUS_LABELS[record.rule_status] || '판단 불확실'}
        </span>
      </div>
      <dl className="traffic-shadow-metrics">
        <div><dt>복제본</dt><dd>{record.current_replica_count}</dd></div>
        <div><dt>RPS · 1분</dt><dd>{record.current_1m?.rps?.toFixed(2) ?? '—'}</dd></div>
        <div><dt>RPS · 5분</dt><dd>{record.current_5m?.rps?.toFixed(2) ?? '—'}</dd></div>
        <div><dt>CPU 평균</dt><dd>{cpuText}</dd></div>
        <div><dt>노드 부하 최대</dt><dd>{nodeLoadText}</dd></div>
        <div><dt>HTTP 오류율</dt><dd>{formatPercent(record.current_1m?.error_ratio)}</dd></div>
      </dl>
      <div className="traffic-shadow-decisions" aria-label="실제 및 Shadow 판단">
        <span>{ACTUAL_DECISION_LABELS[record.actual_fleet_decision] || '실제: 알 수 없음'}</span>
        <span>{SHADOW_DECISION_LABELS[record.shadow_decision] || 'Shadow: 알 수 없음'}</span>
        <strong data-match={record.decisions_match === null ? 'unknown' : record.decisions_match ? 'yes' : 'no'}>
          {record.decisions_match === null
            ? '비교 불가'
            : record.decisions_match
              ? '판단 일치'
              : '판단 불일치'}
        </strong>
      </div>
      <p className="traffic-shadow-rationale">
        {rationale.length ? rationale.join(' · ') : '판단 근거가 없습니다.'}
        {record.data_quality !== 'complete' && ` · 데이터 품질: ${QUALITY_LABELS[record.data_quality] || '확인 필요'}`}
      </p>
    </article>
  );
}

export default function TrafficShadowPanel({ state }) {
  const records = state?.records || [];
  let message = null;
  let tone = 'info';
  if (state?.status === 'loading') {
    message = 'Shadow 관측 결과를 불러오는 중입니다.';
  } else if (state?.status === 'error') {
    tone = state.error?.kind === 'auth' ? 'warning' : 'error';
    message = state.error?.kind === 'auth'
      ? '인증 오류: API 토큰을 확인해 주세요.'
      : state.error?.kind === 'network'
        ? '백엔드 연결을 확인할 수 없습니다.'
        : state.error?.kind === 'response'
          ? '서버 응답을 확인할 수 없습니다.'
          : state.error?.status === 503
            ? '서버 인증 설정을 확인할 수 없습니다.'
          : 'Shadow 결과를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.';
  } else if (state?.status === 'ready' && state.enabled === false) {
    message = 'Shadow Mode가 비활성화되어 있습니다.';
  } else if (state?.status === 'ready' && records.length === 0) {
    message = '아직 비교 결과가 없습니다. 표본이 쌓이면 여기에 표시됩니다.';
  }

  return (
    <section className="traffic-shadow-panel" aria-labelledby="traffic-shadow-title">
      <header className="traffic-shadow-header">
        <div>
          <h3 id="traffic-shadow-title">Fleet Shadow Mode</h3>
          <p>CPU 기반 Fleet 판단과 트래픽 기반 가상 판단 비교</p>
        </div>
        <span className="traffic-shadow-observation">관측 전용 · 서버 제어 안 함</span>
      </header>
      {message && <p className="traffic-shadow-message" data-tone={tone} role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'}>{message}</p>}
      {state?.status === 'ready' && state.enabled && records.length > 0 && (
        <div className="traffic-shadow-grid">
          {records.map((record) => <IncidentCard key={record.event_id} record={record} />)}
        </div>
      )}
      <p className="traffic-shadow-footnote">
        Shadow 판단은 분석·권고에만 사용되며 확장, 축소, 라우팅 변경 또는 차단을 실행하지 않습니다.
        인증 실패율은 401/403 상태 코드 기반 대리 지표입니다.
      </p>
    </section>
  );
}
