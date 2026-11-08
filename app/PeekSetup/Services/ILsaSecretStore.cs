namespace PeekSetup.Services
{
    public interface ILsaSecretStore
    {
        bool StoreSecret(string keyName, byte[] payload);
        bool DeleteSecret(string keyName);
        byte[]? RetrieveSecret(string keyName);
    }
}
