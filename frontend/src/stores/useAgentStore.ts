/**
 * Lab assistant chat. A request runs on the server and keeps going whatever
 * the page does; this store *follows* one conversation at a time (the open
 * lab's): it streams the request it sent, or attaches to one already running
 * (after a reload, or coming back to the lab). Leaving a lab only stops
 * following — Stop is what stops the assistant.
 */
import { create } from 'zustand';
import { api, attachAgentConversation, streamAgentChat, type AgentConversationSummary, type AgentEvent,
  type AIStatus } from '../utils/api';
import { useLabStore } from './useLabStore';

export type Part =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; label: string; state: 'running' | 'done' | 'error'; summary?: string };

export interface ChatMessage { role: 'user' | 'assistant'; parts: Part[]; error?: string }

/** What the chat showed in a lab, kept while you're elsewhere. */
interface LabView { conversationId: string | null; messages: ChatMessage[] }

interface AgentStore {
  open: boolean;
  status: AIStatus | null;
  model: string;
  topologyId: number | null;
  conversationId: string | null;
  messages: ChatMessage[];
  conversations: AgentConversationSummary[];
  /** The followed conversation has a request running (on the server). */
  busy: boolean;
  activity: string;            // live status line ("Searching Cisco documentation")
  abort: AbortController | null;
  views: Record<number, LabView>;

  setOpen: (v: boolean) => void;
  loadStatus: () => Promise<void>;
  setModel: (m: string) => void;
  send: (text: string) => Promise<void>;
  stop: () => void;
  newChat: () => void;
  /** Follow the open lab: keep the old lab's view, show this one's, pick up a running request. */
  syncLab: () => Promise<void>;
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

const STOP_FOLLOWING = { busy: false, activity: '', abort: null };

export const useAgentStore = create<AgentStore>((set, get) => {
  /** Stop following the current stream (the request keeps running on the server). */
  const detach = () => {
    get().abort?.abort();
    set(STOP_FOLLOWING);
  };

  /** Apply one event of the followed request to the last (assistant) message. */
  const onEvent = (e: AgentEvent) => {
    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set({ messages: get().messages.map((m, i, all) => (i === all.length - 1 ? fn(m) : m)) });
    if (e.type === 'conversation') set({ conversationId: e.id });
    else if (e.type === 'snapshot') {
      set({ conversationId: e.id, messages: e.messages as ChatMessage[],
            ...(get().status?.models.some(m => m.id === e.model) ? { model: e.model } : {}) });
      if (!e.running) set({ busy: false, activity: '' });
    } else if (e.type === 'text') {
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

  /** Follow a stream until it ends (or we stop following it). */
  const follow = async (start: (signal: AbortSignal) => Promise<void>) => {
    const abort = new AbortController();
    set({ busy: true, activity: get().activity || 'Thinking', abort });
    const tid = get().topologyId;
    try {
      await start(abort.signal);
    } catch (e) {
      if ((e as Error).name !== 'AbortError' && get().abort === abort) {
        set({ messages: get().messages.map((m, i, all) => (i === all.length - 1 ? { ...m, error: (e as Error).message } : m)) });
      }
    } finally {
      // Only the stream we're following owns the busy state (an interrupted
      // or abandoned stream can end after the next one has started).
      if (get().abort === abort) set(STOP_FOLLOWING);
      if (get().topologyId === tid) {
        refreshLab();
        get().loadConversations();
      }
    }
  };

  const attach = (tid: number, cid: string) =>
    follow(signal => attachAgentConversation(tid, cid, onEvent, signal));

  return {
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
    views: {},

    setOpen: (open) => {
      set({ open });
      if (open && !get().status) get().loadStatus();
      if (open) get().syncLab();
    },

    syncLab: async () => {
      const tid = useLabStore.getState().currentTopologyId;
      if (!tid) return;
      const prev = get().topologyId;
      if (prev !== tid) {
        detach();                                // the old lab's request keeps running
        const views = { ...get().views };
        if (prev) views[prev] = { conversationId: get().conversationId, messages: get().messages };
        const v = views[tid] ?? { conversationId: null, messages: [] };
        set({ views, topologyId: tid, conversationId: v.conversationId, messages: v.messages, conversations: [] });
      }
      await get().loadConversations();
      // Pick a request still running in this lab back up: the one shown, else the newest.
      if (get().busy || get().topologyId !== tid) return;
      const running = get().conversations.filter(c => c.running);
      const pick = running.find(c => c.id === get().conversationId) ?? running[0];
      if (pick) attach(tid, pick.id);
    },

    loadConversations: async () => {
      const tid = get().topologyId ?? useLabStore.getState().currentTopologyId;
      if (!tid) return;
      try {
        const list = await api.listAgentConversations(tid);
        if (get().topologyId === tid || get().topologyId === null) set({ conversations: list });
      } catch { /* keep the old list */ }
    },

    openConversation: async (id) => {
      const tid = useLabStore.getState().currentTopologyId;
      if (!tid) return;
      if (get().conversationId === id && get().busy) return;
      detach();                                  // a request in the one we leave keeps running
      try {
        const c = await api.getAgentConversation(tid, id);
        set({ topologyId: tid, conversationId: c.id, messages: c.messages as ChatMessage[], activity: '',
              // Continue on the model the conversation was held with.
              ...(get().status?.models.some(m => m.id === c.model) ? { model: c.model } : {}) });
        if (c.running) attach(tid, c.id);
      } catch { /* deleted meanwhile */ get().loadConversations(); }
    },

    deleteConversation: async (id) => {
      const tid = useLabStore.getState().currentTopologyId;
      if (!tid) return;
      if (get().conversationId === id && get().busy) get().stop();
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

    // A fresh conversation; earlier ones stay saved and listed (a running one keeps running).
    newChat: () => {
      detach();
      set({ conversationId: null, messages: [], activity: '' });
    },

    stop: () => {
      const { conversationId, topologyId } = get();
      // Tell the server to stop now, then stop listening.
      if (conversationId && topologyId) api.stopAgent(topologyId, conversationId).catch(() => {});
      detach();
    },

    send: async (text) => {
      const tid = useLabStore.getState().currentTopologyId;
      if (!tid || !text.trim()) return;
      if (get().topologyId !== tid) await get().syncLab();
      // A new message while it's working interrupts the current request; the
      // server stops it and runs this one as soon as it has wound down.
      if (get().busy) get().stop();
      if (!get().status) await get().loadStatus();
      set({
        open: true, activity: 'Thinking',
        messages: [...get().messages, { role: 'user', parts: [{ kind: 'text', text }] }, { role: 'assistant', parts: [] }],
      });
      await follow(signal => streamAgentChat(
        tid, { message: text, conversation_id: get().conversationId, model: get().model }, onEvent, signal));
    },
  };
});
