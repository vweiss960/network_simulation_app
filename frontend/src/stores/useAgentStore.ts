/**
 * Lab assistant chat: messages for the open lab, the streaming connection to
 * the agent, the chosen model. One conversation per lab session; switching
 * labs starts a fresh one.
 */
import { create } from 'zustand';
import { api, streamAgentChat, type AgentConversationSummary, type AgentEvent, type AIStatus } from '../utils/api';
import { useLabStore } from './useLabStore';

export type Part =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; label: string; state: 'running' | 'done' | 'error'; summary?: string };

export interface ChatMessage { role: 'user' | 'assistant'; parts: Part[]; error?: string }

interface AgentStore {
  open: boolean;
  status: AIStatus | null;
  model: string;
  topologyId: number | null;
  conversationId: string | null;
  messages: ChatMessage[];
  conversations: AgentConversationSummary[];
  busy: boolean;
  activity: string;            // live status line ("Searching Cisco documentation")
  abort: AbortController | null;

  setOpen: (v: boolean) => void;
  loadStatus: () => Promise<void>;
  setModel: (m: string) => void;
  send: (text: string) => Promise<void>;
  stop: () => void;
  newChat: () => void;
  loadConversations: () => Promise<void>;
  openConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
}

let labRefresh: ReturnType<typeof setTimeout> | null = null;
const refreshLab = () => {
  if (labRefresh) clearTimeout(labRefresh);
  labRefresh = setTimeout(() => {
    const s = useLabStore.getState();
    s.refreshTopology();
    s.pollLab();
  }, 250);
};

export const useAgentStore = create<AgentStore>((set, get) => ({
  open: false,
  status: null,
  model: '',
  topologyId: null,
  conversationId: null,
  messages: [],
  conversations: [],
  busy: false,
  activity: '',
  abort: null,

  setOpen: (open) => {
    set({ open });
    if (open && !get().status) get().loadStatus();
    if (open) get().loadConversations();
  },

  loadConversations: async () => {
    const tid = useLabStore.getState().currentTopologyId;
    if (!tid) return;
    if (get().topologyId !== tid) set({ topologyId: tid, conversationId: null, messages: [] });
    try { set({ conversations: await api.listAgentConversations(tid) }); } catch { /* keep the old list */ }
  },

  openConversation: async (id) => {
    const tid = useLabStore.getState().currentTopologyId;
    if (!tid || get().busy) return;
    try {
      const c = await api.getAgentConversation(tid, id);
      set({ topologyId: tid, conversationId: c.id, messages: c.messages as ChatMessage[], activity: '',
            // Continue on the model the conversation was held with.
            ...(get().status?.models.some(m => m.id === c.model) ? { model: c.model } : {}) });
    } catch { /* deleted meanwhile */ get().loadConversations(); }
  },

  deleteConversation: async (id) => {
    const tid = useLabStore.getState().currentTopologyId;
    if (!tid) return;
    await api.deleteAgentConversation(tid, id).catch(() => {});
    if (get().conversationId === id) set({ conversationId: null, messages: [] });
    get().loadConversations();
  },

  loadStatus: async () => {
    try {
      const status = await api.aiStatus();
      set({ status, model: get().model || status.default_model });
    } catch (e) {
      set({ status: { enabled: false, error: (e as Error).message, default_model: '', models: [] } });
    }
  },

  setModel: (model) => {
    // A different model can't continue the other model's conversation.
    if (model !== get().model) get().newChat();
    set({ model });
  },

  // A fresh conversation; earlier ones stay saved and listed.
  newChat: () => {
    if (get().busy) get().stop();
    set({ conversationId: null, messages: [], activity: '' });
  },

  stop: () => {
    const { conversationId, topologyId, abort } = get();
    // Tell the server to stop between steps, then stop listening.
    if (conversationId && topologyId) api.stopAgent(topologyId, conversationId).catch(() => {});
    abort?.abort();
    set({ busy: false, activity: '', abort: null });
  },

  send: async (text) => {
    const tid = useLabStore.getState().currentTopologyId;
    if (!tid || !text.trim() || get().busy) return;
    if (get().topologyId !== tid) {           // a different lab: fresh conversation
      set({ topologyId: tid, conversationId: null, messages: [] });
    }
    if (!get().status) await get().loadStatus();
    const abort = new AbortController();
    set({
      open: true, busy: true, activity: 'Thinking', abort,
      messages: [...get().messages, { role: 'user', parts: [{ kind: 'text', text }] }, { role: 'assistant', parts: [] }],
    });
    // Update the last (assistant) message in place.
    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set({ messages: get().messages.map((m, i, all) => (i === all.length - 1 ? fn(m) : m)) });

    const onEvent = (e: AgentEvent) => {
      if (e.type === 'conversation') set({ conversationId: e.id });
      else if (e.type === 'text') {
        set({ activity: '' });
        patch(m => {
          const parts = [...m.parts];
          const last = parts[parts.length - 1];
          if (last?.kind === 'text') parts[parts.length - 1] = { kind: 'text', text: last.text + e.delta };
          else parts.push({ kind: 'text', text: e.delta });
          return { ...m, parts };
        });
      } else if (e.type === 'tool') {
        set({ activity: e.state === 'running' ? e.label : 'Thinking' });
        patch(m => {
          const i = m.parts.findIndex(p => p.kind === 'tool' && p.id === e.id);
          const part: Part = { kind: 'tool', id: e.id, label: e.label, state: e.state, summary: e.summary };
          const parts = [...m.parts];
          if (i >= 0) parts[i] = part; else parts.push(part);
          return { ...m, parts };
        });
      } else if (e.type === 'status') set({ activity: e.text });
      else if (e.type === 'lab_changed') refreshLab();
      else if (e.type === 'error') patch(m => ({ ...m, error: e.message }));
    };

    try {
      await streamAgentChat(tid, { message: text, conversation_id: get().conversationId, model: get().model },
                            onEvent, abort.signal);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') patch(m => ({ ...m, error: (e as Error).message }));
    } finally {
      set({ busy: false, activity: '', abort: null });
      refreshLab();
      get().loadConversations();
    }
  },
}));
