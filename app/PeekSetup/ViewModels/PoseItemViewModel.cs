using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace PeekSetup.ViewModels
{
    public enum PoseState
    {
        Pending,
        Active,
        Completed
    }

    public class PoseItemViewModel : INotifyPropertyChanged
    {
        private PoseState _state;

        public string PoseKey { get; }
        public string Label { get; }
        public int Index { get; }

        public PoseState State
        {
            get => _state;
            set
            {
                if (_state != value)
                {
                    _state = value;
                    OnPropertyChanged();
                    OnPropertyChanged(nameof(IsCompleted));
                    OnPropertyChanged(nameof(IsActive));
                    OnPropertyChanged(nameof(IsPending));
                }
            }
        }

        public bool IsCompleted => State == PoseState.Completed;
        public bool IsActive => State == PoseState.Active;
        public bool IsPending => State == PoseState.Pending;

        public PoseItemViewModel(string poseKey, string label, int index)
        {
            PoseKey = poseKey;
            Label = label;
            Index = index;
            _state = index == 0 ? PoseState.Active : PoseState.Pending;
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
