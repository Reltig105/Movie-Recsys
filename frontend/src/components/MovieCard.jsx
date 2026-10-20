import { useState } from 'react'
import api from '../api'

function MovieCard({ movie, currentUserId }) {
  const [userRating, setUserRating] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  if (!movie) return null

  const handleRate = async (value) => {
    setUserRating(value)
    setSaving(true)
    setError(null)

    try {
      await api.post('/rate', {
        user_id: currentUserId,
        movie_id: movie.movie_id,
        rating: value,
      })
    } catch (err) {
      console.error('Не удалось сохранить оценку:', err)
      setError('Оценка не сохранена')
    } finally {
      setSaving(false)
    }
  }

  return (
    <article className="movie-card">
      <img
        className="movie-poster"
        src={`https://placehold.co/200x300/2b2b2b/FFFFFF/png?text=${encodeURIComponent(movie.title)}`}
        alt={movie.title}
        width="200"
        height="300"
        loading="lazy"
      />
      <div className="movie-info">
        <h2 className="movie-title">{movie.title}</h2>
        <p className="movie-genres">{movie.genres}</p>

        <div className="rating" role="group" aria-label="Оценка фильма">
          {[1, 2, 3, 4, 5].map((star) => (
            <button
              key={star}
              type="button"
              className={`star${userRating >= star ? ' star--on' : ''}`}
              onClick={() => handleRate(star)}
              disabled={saving}
              aria-label={`Оценка ${star}`}
            >
              ★
            </button>
          ))}
        </div>

        {error && <p className="rating-error">{error}</p>}
      </div>
    </article>
  )
}

export default MovieCard
