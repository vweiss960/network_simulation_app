import { useState, useEffect } from 'react';
import { api } from '../../utils/api';
import { useAuthStore } from '../../stores/useAuthStore';
import toast from 'react-hot-toast';
import { Plus, Trash2, Edit3, Save, Shield, X } from 'lucide-react';

interface UserRecord {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  is_local: boolean;
  auth_source: string;
}

export default function UserManagementPanel() {
  const { user: currentUser } = useAuthStore();
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState({ username: '', password: '', display_name: '', is_admin: false });
  const [editForm, setEditForm] = useState({ password: '', display_name: '', is_admin: false });

  const loadUsers = async () => {
    try {
      const data = await api.listUsers();
      setUsers(data);
    } catch { /* ignore if not admin */ }
  };

  useEffect(() => { loadUsers(); }, []);

  const handleAdd = async () => {
    if (!form.username || !form.password) { toast.error('Username and password required'); return; }
    try {
      await api.createUser({
        username: form.username,
        password: form.password,
        display_name: form.display_name || form.username,
        is_admin: form.is_admin,
      });
      setForm({ username: '', password: '', display_name: '', is_admin: false });
      setShowAdd(false);
      loadUsers();
      toast.success('User created');
    } catch (e: any) {
      toast.error(e.message || 'Failed to create user');
    }
  };

  const startEdit = (u: UserRecord) => {
    setEditingId(u.id);
    setEditForm({ password: '', display_name: u.display_name, is_admin: u.is_admin });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      const data: any = { display_name: editForm.display_name, is_admin: editForm.is_admin };
      if (editForm.password) data.password = editForm.password;
      await api.updateUser(editingId, data);
      setEditingId(null);
      loadUsers();
      toast.success('User updated');
    } catch { toast.error('Failed to update'); }
  };

  const handleDelete = async (u: UserRecord) => {
    if (!confirm(`Delete user "${u.username}"?`)) return;
    try {
      await api.deleteUser(u.id);
      loadUsers();
      toast.success('User deleted');
    } catch (e: any) { toast.error(e.message || 'Failed to delete'); }
  };

  if (!currentUser?.is_admin) {
    return <div className="text-sm text-surface-500 text-center py-8">Admin access required</div>;
  }

  return (
    <div className="space-y-3">
      <button className="btn btn-primary btn-sm w-full" onClick={() => setShowAdd(!showAdd)}>
        <Plus size={14} /> Add Local User
      </button>

      {showAdd && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in">
          <input className="w-full" placeholder="Username" value={form.username}
            onChange={e => setForm({ ...form, username: e.target.value })} />
          <input className="w-full" placeholder="Password" type="password" value={form.password}
            onChange={e => setForm({ ...form, password: e.target.value })} />
          <input className="w-full" placeholder="Display name (optional)" value={form.display_name}
            onChange={e => setForm({ ...form, display_name: e.target.value })} />
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={form.is_admin}
              onChange={e => setForm({ ...form, is_admin: e.target.checked })}
              className="accent-primary-500" />
            Admin privileges
          </label>
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleAdd}>Create</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="space-y-1">
        {users.map(u => (
          <div key={u.id} className="rounded-lg border border-surface-700 bg-surface-800/50 px-3 py-2">
            {editingId === u.id ? (
              <div className="space-y-2">
                <input className="w-full text-sm" placeholder="Display name" value={editForm.display_name}
                  onChange={e => setEditForm({ ...editForm, display_name: e.target.value })} />
                <input className="w-full text-sm" placeholder="New password (leave blank to keep)" type="password"
                  value={editForm.password}
                  onChange={e => setEditForm({ ...editForm, password: e.target.value })} />
                <label className="flex items-center gap-2 text-xs">
                  <input type="checkbox" checked={editForm.is_admin}
                    onChange={e => setEditForm({ ...editForm, is_admin: e.target.checked })}
                    className="accent-primary-500" />
                  Admin
                </label>
                <div className="flex gap-2">
                  <button className="btn btn-primary btn-sm flex-1" onClick={saveEdit}><Save size={12} /> Save</button>
                  <button className="btn btn-secondary btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium flex items-center gap-1.5">
                    {u.username}
                    {u.is_admin && <Shield size={11} className="text-amber-400" />}
                  </div>
                  <div className="text-[10px] text-surface-500">
                    {u.display_name} &middot; {u.auth_source}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button className="btn btn-ghost btn-sm p-1" onClick={() => startEdit(u)}><Edit3 size={12} /></button>
                  {u.username !== currentUser?.username && (
                    <button className="btn btn-ghost btn-sm p-1 text-red-400" onClick={() => handleDelete(u)}>
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        {users.length === 0 && (
          <div className="text-center py-4 text-surface-500 text-sm">No users found</div>
        )}
      </div>
    </div>
  );
}
