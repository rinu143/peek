using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.AccessControl;
using System.Security.Principal;
using Microsoft.Win32;

namespace PeekSetup.Services
{
    public class RegistryAuthorizationStore : IAuthorizationStore
    {
        public const string BaseKeyPath = @"SOFTWARE\Peek\Identities";
        public const string ValueName = "Authorized";
        public const int MaxAuthorizedIdentities = 5;

        public const RegistryRights ProhibitedWriteRights =
            RegistryRights.SetValue |
            RegistryRights.CreateSubKey |
            RegistryRights.Delete |
            RegistryRights.ChangePermissions |
            RegistryRights.TakeOwnership;

        public static bool ContainsProhibitedWriteRights(RegistryRights rights)
        {
            if ((rights & ProhibitedWriteRights) != 0)
            {
                return true;
            }

            if ((rights & RegistryRights.WriteKey) == RegistryRights.WriteKey ||
                (rights & RegistryRights.FullControl) == RegistryRights.FullControl)
            {
                return true;
            }

            return false;
        }

        private readonly RegistryHive _hive;
        private readonly string _baseKeyPath;

        public RegistryAuthorizationStore(
            RegistryHive hive = RegistryHive.LocalMachine,
            string baseKeyPath = BaseKeyPath)
        {
            _hive = hive;
            _baseKeyPath = baseKeyPath;
        }

        public static string GetKeyPath(string userSid)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                throw new ArgumentException("User SID cannot be null or empty", nameof(userSid));

            return $@"{BaseKeyPath}\{userSid}";
        }

        public string ResolveKeyPath(string userSid)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                throw new ArgumentException("User SID cannot be null or empty", nameof(userSid));

            return $@"{_baseKeyPath}\{userSid}";
        }

        private RegistryKey OpenBaseHive()
        {
            return RegistryKey.OpenBaseKey(_hive, RegistryView.Registry64);
        }

        public string[] GetAuthorizedIdentities(string userSid)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                return Array.Empty<string>();

            try
            {
                using var hklm = OpenBaseHive();
                using var key = hklm.OpenSubKey(ResolveKeyPath(userSid), false);
                if (key == null)
                    return Array.Empty<string>();

                object? value = key.GetValue(ValueName);
                if (value is string[] array)
                {
                    return array
                        .Where(id => !string.IsNullOrWhiteSpace(id))
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .Take(MaxAuthorizedIdentities)
                        .ToArray();
                }
                else if (value is string single && !string.IsNullOrWhiteSpace(single))
                {
                    return new[] { single };
                }

                return Array.Empty<string>();
            }
            catch
            {
                return Array.Empty<string>();
            }
        }

        public bool SetAuthorizedIdentities(string userSid, string[] profileIds)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                return false;

            var filtered = (profileIds ?? Array.Empty<string>())
                .Where(id => !string.IsNullOrWhiteSpace(id))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Take(MaxAuthorizedIdentities)
                .ToArray();

            string path = ResolveKeyPath(userSid);
            RegistryKey? key = null;
            try
            {
                using var hklm = OpenBaseHive();
                key = hklm.CreateSubKey(path, true);
                if (key == null)
                    return false;

                try
                {
                    EnforceSecureDacl(key);

                    if (HasInsecureWriteRights(key))
                    {
                        Trace.TraceError($"Insecure write rights remain on registry key {path}. Deleting key.");
                        key?.Close();
                        key = null;
                        DeleteIdentitiesKey(userSid);
                        return false;
                    }
                }
                catch (Exception daclEx)
                {
                    Trace.TraceError($"EnforceSecureDacl failed for {path}: {daclEx}");
                    key?.Close();
                    key = null;
                    DeleteIdentitiesKey(userSid);
                    return false;
                }

                key?.SetValue(ValueName, filtered, RegistryValueKind.MultiString);
                return true;
            }
            catch (Exception ex)
            {
                Trace.TraceError($"SetAuthorizedIdentities failed for {userSid}: {ex}");
                return false;
            }
            finally
            {
                key?.Dispose();
            }
        }

        public bool AddAuthorizedIdentity(string userSid, string profileId)
        {
            if (string.IsNullOrWhiteSpace(userSid) || string.IsNullOrWhiteSpace(profileId))
                return false;

            var existing = GetAuthorizedIdentities(userSid).ToList();
            if (existing.Contains(profileId, StringComparer.OrdinalIgnoreCase))
            {
                return true; // Already present
            }

            if (existing.Count >= MaxAuthorizedIdentities)
            {
                return false; // Exceeds cap
            }

            existing.Add(profileId);
            return SetAuthorizedIdentities(userSid, existing.ToArray());
        }

        public bool RemoveAuthorizedIdentity(string userSid, string profileId)
        {
            if (string.IsNullOrWhiteSpace(userSid) || string.IsNullOrWhiteSpace(profileId))
                return false;

            var existing = GetAuthorizedIdentities(userSid).ToList();
            existing.RemoveAll(id => string.Equals(id, profileId, StringComparison.OrdinalIgnoreCase));

            if (existing.Count == 0)
            {
                DeleteIdentitiesKey(userSid);
                return true;
            }

            return SetAuthorizedIdentities(userSid, existing.ToArray());
        }

        public bool DeleteIdentitiesKey(string userSid)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                return false;

            try
            {
                using var hklm = OpenBaseHive();
                string path = ResolveKeyPath(userSid);
                hklm.DeleteSubKeyTree(path, false);
                return true;
            }
            catch
            {
                return false;
            }
        }

        public bool HasAuthorizedIdentities(string userSid)
        {
            var ids = GetAuthorizedIdentities(userSid);
            return ids != null && ids.Length > 0;
        }

        public static bool HasInsecureWriteRights(RegistryKey key)
        {
            var security = key.GetAccessControl();
            var usersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);
            var worldSid = new SecurityIdentifier(WellKnownSidType.WorldSid, null);

            foreach (RegistryAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            {
                if (rule.IdentityReference.Equals(usersSid) || rule.IdentityReference.Equals(worldSid))
                {
                    if (rule.AccessControlType == AccessControlType.Allow)
                    {
                        if (ContainsProhibitedWriteRights(rule.RegistryRights))
                        {
                            return true;
                        }
                    }
                }
            }

            return false;
        }

        public static void EnforceSecureDacl(RegistryKey key)
        {
            var security = key.GetAccessControl();
            bool usersCanWrite = false;

            var usersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);
            var worldSid = new SecurityIdentifier(WellKnownSidType.WorldSid, null);

            foreach (RegistryAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            {
                if (rule.IdentityReference.Equals(usersSid) || rule.IdentityReference.Equals(worldSid))
                {
                    if (rule.AccessControlType == AccessControlType.Allow)
                    {
                        if (ContainsProhibitedWriteRights(rule.RegistryRights))
                        {
                            usersCanWrite = true;
                            break;
                        }
                    }
                }
            }

            if (usersCanWrite)
            {
                // Restrict explicitly: SYSTEM & Administrators full, Users read only
                var secureDacl = new RegistrySecurity();
                var systemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
                var adminSid = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);

                secureDacl.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);

                secureDacl.AddAccessRule(new RegistryAccessRule(
                    systemSid,
                    RegistryRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None,
                    AccessControlType.Allow));

                secureDacl.AddAccessRule(new RegistryAccessRule(
                    adminSid,
                    RegistryRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None,
                    AccessControlType.Allow));

                secureDacl.AddAccessRule(new RegistryAccessRule(
                    usersSid,
                    RegistryRights.ReadKey,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None,
                    AccessControlType.Allow));

                key.SetAccessControl(secureDacl);
            }
        }
    }
}
