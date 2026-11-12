using System;
using System.Security.Cryptography;
using PeekUpdate.Models;

namespace PeekUpdate.Services
{
    public class SignatureVerifier
    {
        // Embedded public keys (BCRYPT_ECCKEY_BLOB / EccPublicBlob base64) for key rotation
        public const string PrimaryPublicKeyBase64 = "RUNTMSAAAACu8UFFjKHdTbvgHGY449zdlpwy6asNxlPUnEZkNmbi3A3xKUS06eJQnBFZcLoC9N839PIVPCKVSACOkfJTCo48";
        public const string SecondaryPublicKeyBase64 = "RUNTMSAAAAA5a2Yv3VxEArbVPDhIaj0DIbufNxv/VREWV/O2V0DNLcCJWBF8MvCtF1yA+1J3ZCa+OshHf1cgEUObDKBFSXe+";

        private readonly string[] _trustedPublicKeys;

        public SignatureVerifier(params string[]? customKeys)
        {
            if (customKeys != null && customKeys.Length > 0)
            {
                _trustedPublicKeys = customKeys;
            }
            else
            {
                _trustedPublicKeys = new[] { PrimaryPublicKeyBase64, SecondaryPublicKeyBase64 };
            }
        }

        public bool VerifyManifest(UpdateManifest manifest)
        {
            if (manifest == null || string.IsNullOrWhiteSpace(manifest.Signature))
            {
                return false;
            }

            byte[] signatureBytes;
            try
            {
                signatureBytes = Convert.FromBase64String(manifest.Signature.Trim());
            }
            catch
            {
                return false;
            }

            byte[] canonicalBytes = CanonicalJsonSerializer.SerializeToBytes(manifest);

            foreach (var keyBase64 in _trustedPublicKeys)
            {
                if (VerifySignatureWithKey(canonicalBytes, signatureBytes, keyBase64))
                {
                    return true;
                }
            }

            return false;
        }

        public static bool VerifySignatureWithKey(byte[] data, byte[] signature, string publicKeyBase64)
        {
            try
            {
                byte[] keyBytes = Convert.FromBase64String(publicKeyBase64.Trim());
                using var dsa = CreateECDsaFromPublicKeyBytes(keyBytes);
                if (dsa == null) return false;

                // Support both IEEE P1363 (64 bytes raw r||s) and RFC 3279 DER sequence
                if (dsa.VerifyData(data, signature, HashAlgorithmName.SHA256, DSASignatureFormat.IeeeP1363FixedFieldConcatenation))
                {
                    return true;
                }

                if (dsa.VerifyData(data, signature, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence))
                {
                    return true;
                }

                return false;
            }
            catch
            {
                return false;
            }
        }

        public static ECDsa? CreateECDsaFromPublicKeyBytes(byte[] keyBytes)
        {
            // Case 1: BCRYPT_ECCKEY_BLOB (72 bytes: 8-byte header 'ECS1', 32-byte X, 32-byte Y)
            if (keyBytes.Length == 72 && keyBytes[0] == 0x45 && keyBytes[1] == 0x43 && keyBytes[2] == 0x53 && keyBytes[3] == 0x31)
            {
                byte[] x = new byte[32];
                byte[] y = new byte[32];
                Buffer.BlockCopy(keyBytes, 8, x, 0, 32);
                Buffer.BlockCopy(keyBytes, 40, y, 0, 32);

                var ecParams = new ECParameters
                {
                    Curve = ECCurve.NamedCurves.nistP256,
                    Q = new ECPoint { X = x, Y = y }
                };
                return ECDsa.Create(ecParams);
            }

            // Case 2: SubjectPublicKeyInfo (SPKI, typically 91 bytes for P-256)
            try
            {
                var dsa = ECDsa.Create();
                dsa.ImportSubjectPublicKeyInfo(keyBytes, out _);
                return dsa;
            }
            catch
            {
                // Fallback
            }

            return null;
        }
    }
}
