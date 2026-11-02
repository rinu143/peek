using System.Collections.Generic;
using System.Threading.Tasks;

namespace PeekSetup.Services
{
    public class ProfileModel
    {
        public string ProfileId { get; set; } = string.Empty;
        public string DisplayName { get; set; } = string.Empty;
        public bool Enabled { get; set; } = true;
        public bool Authorized { get; set; }
        public int? QualityPercentage { get; set; }
        public List<double> PoseQualities { get; set; } = new();
        public int TemplateCount { get; set; }
        public double CreatedAt { get; set; }
        public string? UserSid { get; set; }
    }

    public interface IProfileManager
    {
        Task<List<ProfileModel>> GetProfilesAsync();
        Task<bool> ToggleProfileAsync(string profileId, bool enabled);
        Task<bool> DeleteProfileAsync(string profileId);
        Task<bool> RenameProfileAsync(string profileId, string newDisplayName);
        Task<bool> SaveProfileAsync(string profileId, byte[] encryptedData) => Task.FromResult(true);
    }
}
