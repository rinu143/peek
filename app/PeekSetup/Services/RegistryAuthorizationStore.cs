using System;
using System.Collections.Generic;
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

        public static string GetKeyPath(string userSid)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                throw new ArgumentException("User SID cannot be null or empty", nameof(userSid));

            return $@"{BaseKeyPath}\{userSid}";
        }

        public string[] GetAuthorizedIdentities(string userSid)
        {
            if (string.IsNullOrWhiteSpace(userSid))
                return Array.Empty<string>();

            try
            {
                using var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
                using var key = hklm.OpenSubKey(GetKeyPath(userSid), false);
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

            try
            {
                using var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
                string path = GetKeyPath(userSid);
                using var key = hklm.CreateSubKey(path, true);
                if (key == null)
                    return false;

                EnforceSecureDacl(key);

                key.SetValue(ValueName, filtered, RegistryValueKind.MultiString);
                return true;
            }
            catch
            {
                return false;
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
                using var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
                string path = GetKeyPath(userSid);
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

        public static void EnforceSecureDacl(RegistryKey key)
        {
            try
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
                            var writeMask = RegistryRights.SetValue | RegistryRights.CreateSubKey | RegistryRights.WriteKey | RegistryRights.FullControl;
                            if ((rule.RegistryRights & writeMask) != 0)
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
            catch
            {
                // In non-elevated or simulated environments, ignore access control errors
            }
        }
    }
}
