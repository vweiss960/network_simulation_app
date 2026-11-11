import { useState, useRef, useEffect } from 'react';
import { useStore } from '../../stores/useStore';
import { Send, Trash2, Bot, User, Loader2 } from 'lucide-react';

export default function ChatPanel() {
  const { chatMessages, isChatLoading, sendChatMessage, clearChat } = useStore();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages, isChatLoading]);

  const handleSend = () => {
    const msg = input.trim();
    if (!msg || isChatLoading) return;
    setInput('');
    sendChatMessage(msg);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full -m-3">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {chatMessages.length === 0 && (
          <div className="text-center py-8 space-y-3">
            <Bot size={32} className="mx-auto text-primary-400" />
            <div>
              <p className="text-sm text-surface-300 font-medium">Phanero AI Assistant</p>
              <p className="text-xs text-surface-500 mt-1">
                I can help you build and analyze your network topology. Try asking me to:
              </p>
            </div>
            <div className="space-y-1.5 text-left max-w-[280px] mx-auto">
              {[
                'Add 3 Cisco ASR 9001 routers in a triangle topology',
                'Create a flow from node A to node C with 5Gbps',
                'Run a simulation and tell me about any SPOFs',
                'What is ECMP and when should I use it?',
                'Run a Monte Carlo simulation with 50k iterations',
                'Remove the link between router-1 and router-2',
              ].map((suggestion, i) => (
                <button
                  key={i}
                  className="w-full text-left text-[11px] px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-surface-400 hover:text-surface-200 transition-colors"
                  onClick={() => {
                    setInput(suggestion);
                    inputRef.current?.focus();
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {chatMessages.map((msg, i) => (
          <div key={i} className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {msg.role === 'assistant' && (
              <div className="w-6 h-6 rounded-full bg-primary-600 flex items-center justify-center shrink-0 mt-0.5">
                <Bot size={14} className="text-white" />
              </div>
            )}
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-[13px] leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-primary-600 text-white'
                  : 'bg-surface-800 text-surface-200'
              }`}
            >
              {msg.role === 'assistant' ? (
                <div className="whitespace-pre-wrap chat-content">
                  {formatMessage(msg.content)}
                </div>
              ) : (
                <div className="whitespace-pre-wrap">{msg.content}</div>
              )}
            </div>
            {msg.role === 'user' && (
              <div className="w-6 h-6 rounded-full bg-surface-600 flex items-center justify-center shrink-0 mt-0.5">
                <User size={14} className="text-white" />
              </div>
            )}
          </div>
        ))}

        {isChatLoading && (
          <div className="flex gap-2 items-start">
            <div className="w-6 h-6 rounded-full bg-primary-600 flex items-center justify-center shrink-0">
              <Bot size={14} className="text-white" />
            </div>
            <div className="bg-surface-800 rounded-lg px-3 py-2">
              <div className="flex items-center gap-2 text-surface-400 text-xs">
                <Loader2 size={14} className="animate-spin" />
                Thinking...
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-surface-700 p-3">
        {chatMessages.length > 0 && (
          <div className="flex justify-end mb-2">
            <button
              className="btn btn-ghost btn-sm text-[10px] text-surface-500"
              onClick={clearChat}
            >
              <Trash2 size={10} /> Clear chat
            </button>
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            className="flex-1 resize-none text-[13px] min-h-[36px] max-h-[120px]"
            placeholder="Ask me anything about your network..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={isChatLoading}
          />
          <button
            className="btn btn-primary btn-sm px-3 self-end"
            onClick={handleSend}
            disabled={!input.trim() || isChatLoading}
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

function formatMessage(text: string): JSX.Element {
  // Simple markdown-like formatting
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\n)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i} className="font-semibold text-surface-100">{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={i} className="bg-surface-700 px-1 py-0.5 rounded text-primary-300 text-[11px]">{part.slice(1, -1)}</code>;
        }
        if (part === '\n') {
          return <br key={i} />;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}
