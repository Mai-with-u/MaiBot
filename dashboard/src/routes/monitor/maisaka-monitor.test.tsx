import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { PlannerFinalizedEvent } from '@/lib/maisaka-monitor-client'
import { TimelineEventRenderer } from './maisaka-monitor'

const plannerEvent: PlannerFinalizedEvent = {
  session_id: 'session-a',
  cycle_id: 1,
  timestamp: 100,
  timing_gate: null,
  request: null,
  planner: {
    content: 'private planner body',
    reasoning: 'private reasoning',
    model_name: 'model-a',
    tool_calls: [{ id: 'hidden-call', name: 'wait', arguments: { seconds: 100 } }],
    prompt_tokens: 10,
    completion_tokens: 5,
    total_tokens: 15,
    duration_ms: 50,
  },
  tools: [{
    tool_name: 'wait',
    tool_call_id: 'hidden-call',
    tool_args: { seconds: 100 },
    summary: 'private result',
    success: true,
    duration_ms: 100,
  }],
  final_state: { time_records: {}, agent_state: 'wait', end_detail: 'private end detail' },
}

function renderPlanner(
  type: 'planner.progress' | 'planner.finalized',
  data: PlannerFinalizedEvent,
  roundOpen = true,
) {
  return render(
    <TimelineEventRenderer
      entry={{ id: 'evt_1', type, data, timestamp: 100, sessionId: 'session-a' }}
      roundOpen={roundOpen}
      onRoundOpenChange={vi.fn()}
      onOpenReasoning={vi.fn()}
      onJumpToMessage={vi.fn()}
      waitUntil={200}
    />,
  )
}

describe('Planner timeline rendering', () => {
  it.each(['planner.progress', 'planner.finalized'] as const)(
    '%s shows only the supplied names, whether the round is open or collapsed',
    (type) => {
      const data = {
        ...plannerEvent,
        active_tool_call_id: 'hidden-call',
        private_screen_tool_names: ['wait', 'reply'],
      }
      const expanded = renderPlanner(type, data)
      expect(expanded.container.textContent).toBe('wait · reply')
      expect(expanded.container.querySelector('button')).toBeNull()
      const collapsed = renderPlanner(type, { ...data, interrupted: true }, false)
      expect(collapsed.container.textContent).toBe('wait · reply')
    },
  )

  it('renders nothing for an explicitly empty list, without falling back to Planner details', () => {
    const { container } = renderPlanner('planner.finalized', {
      ...plannerEvent,
      private_screen_tool_names: [],
    })
    expect(container).toBeEmptyDOMElement()
  })

  it('still shows ordinary Planner details when the root field is absent', () => {
    const { getByText } = renderPlanner('planner.finalized', plannerEvent)
    expect(getByText('private planner body')).toBeInTheDocument()
    expect(getByText('private end detail')).toBeInTheDocument()
  })
})
